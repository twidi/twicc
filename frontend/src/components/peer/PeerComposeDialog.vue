<script setup>
/**
 * PeerComposeDialog — the owner writes a peer message directly.
 *
 * The deliberately minimal counterpart of the agent path (`peer-send`): title
 * + text to one active peer, nothing else. No attachments, no draft — when
 * the need outgrows this, the user asks an agent, which composes and sends
 * with the full surface. Server-side the send goes through the same service
 * as the agent path, with `author="human"` on the wire (settable only by the
 * owner endpoint), so the receiving side can show and frame the authorship.
 *
 * Mounted once in App.vue, opened by the `twicc:open-peer-compose`
 * CustomEvent (peers manager row, inbox footer, review dialog's Reply).
 * `peerId` preselects the peer; `replyTo` locks it (a reply cannot change
 * peer) and threads the message.
 */
import { ref, computed, watch, nextTick } from 'vue'
import { usePeersStore } from '../../stores/peers'
import { apiFetch } from '../../utils/api'
import { PEER_MESSAGE_TITLE_MAX_CHARS, replySubject } from '../../utils/peerReplyTarget'
import { toast } from '../../composables/useToast'

const props = defineProps({
    open: Boolean,
    // Initial peer selection (manager row, inbox filter, replied message).
    peerId: { type: String, default: null },
    // Message id (pm_…) this one answers; locks the peer select.
    replyTo: { type: String, default: null },
    replyToTitle: { type: String, default: '' },
    // True when the replied message is still awaiting the delivery decision:
    // the dialog then states that replying resolves nothing.
    replyPending: Boolean,
})
const emit = defineEmits(['close'])

const peersStore = usePeersStore()
const dialogRef = ref(null)
const titleInputRef = ref(null)
const textInputRef = ref(null)
const sendButtonRef = ref(null)
const formId = 'peer-compose-form'

const selectedPeerId = ref('')
const title = ref('')
const proposedTitle = ref('')   // what the dialog filled in, to tell it from typing
const text = ref('')
const busy = ref(false)
const error = ref('')
const confirmingDiscard = ref(false)

const activePeers = computed(() => peersStore.peers.filter(p => p.state === 'active'))
const isReply = computed(() => !!props.replyTo)
// What becomes of the answered message once the reply is out (design of
// 2026-09-01): kept open (default), marked done, or refused. Only meaningful
// while it still awaits a decision — a delivered message being answered from
// history must not be flipped. Applied server-side, after the peer accepted
// the reply, so a reply that never left resolves nothing.
const canResolveAnswered = computed(() => isReply.value && props.replyPending)
const resolution = ref('open')   // 'open' | 'done' | 'refused'
// The proposed subject is not something the user typed: closing right after
// opening a reply must not ask them to discard a message they never wrote.
const dirty = computed(() =>
    !!text.value.trim() || title.value.trim() !== proposedTitle.value,
)

// Fresh form on every open — nothing survives a close, by design.
watch(() => props.open, (open) => {
    if (!open) return
    selectedPeerId.value = props.peerId || (activePeers.value.length === 1 ? activePeers.value[0].id : '')
    // A reply proposes the parent's subject: a thread rarely needs a new one,
    // and typing one by hand for "ok, noted" is the friction this composer
    // exists to remove. It stays editable, and required if the parent had none.
    proposedTitle.value = props.replyTo ? replySubject(props.replyToTitle) : ''
    title.value = proposedTitle.value
    resolution.value = 'open'
    text.value = ''
    error.value = ''
    confirmingDiscard.value = false
})

function errorText(payload) {
    const errors = payload?.errors
    if (Array.isArray(errors) && errors.length) {
        return errors.map(e => e.message || e.code).join(' — ')
    }
    return payload?.error || 'Request failed.'
}

async function handleSend() {
    error.value = ''
    const cleanTitle = title.value.trim()
    const cleanText = text.value.trim()
    if (!selectedPeerId.value) {
        error.value = 'Pick a peer.'
        return
    }
    if (!cleanTitle || !cleanText) {
        error.value = 'Title and message are both required.'
        return
    }
    busy.value = true
    try {
        const response = await apiFetch('/api/peer-messages/send/', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                peer_id: selectedPeerId.value,
                title: cleanTitle,
                text: cleanText,
                reply_to: props.replyTo || undefined,
                resolve_reply_to: canResolveAnswered.value && resolution.value !== 'open'
                    ? resolution.value
                    : undefined,
            }),
        })
        const payload = await response.json().catch(() => null)
        if (!response.ok) {
            error.value = errorText(payload)
            return
        }
        const label = peersStore.peerLabel(selectedPeerId.value)
        toast.success(`Message sent to ${label} — awaiting their approval.`)
        // The reply left either way; a resolution that could not apply is
        // reported, not hidden — the message is still in the inbox to fix.
        if (payload?.resolution && !payload.resolution.ok) {
            toast.warning(`Sent, but the answered message could not be marked as ${resolution.value}: ${errorText(payload.resolution)}`)
        }
        // `proposedTitle` too: `dirty` compares the title against it, and a
        // cleared title next to a surviving proposal reads as "typed content"
        // — the close underneath then gets silently vetoed and the emptied
        // dialog lurks under the next modals until it resurfaces.
        title.value = ''
        text.value = ''
        proposedTitle.value = ''
        emit('close')
    } catch {
        error.value = 'Request failed.'
    } finally {
        busy.value = false
    }
}

// Closing with typed content requires a confirmation — there is no draft to
// come back to. `discard` clears first so the hide fired by the parent's
// open=false passes the dirty check.
function attemptClose() {
    if (busy.value) return
    if (dirty.value && !confirmingDiscard.value) {
        confirmingDiscard.value = true
        return
    }
    emit('close')
}

function discard() {
    title.value = ''
    text.value = ''
    proposedTitle.value = ''
    confirmingDiscard.value = false
    emit('close')
}

// Guard against wa-hide bubbling from the nested wa-select, and veto the
// dialog's own hide (Esc, overlay) while content would be lost.
function onHide(event) {
    if (event.target !== dialogRef.value) return
    if (busy.value) {
        event.preventDefault()
        return
    }
    if (dirty.value) {
        event.preventDefault()
        confirmingDiscard.value = true
        return
    }
    emit('close')
}

// wa-button doesn't expose `form` as a property — set the attribute so the
// footer button submits the form (ProjectEditDialog pattern).
function onShow(event) {
    if (event.target !== dialogRef.value) return
    nextTick(() => {
        sendButtonRef.value?.setAttribute('form', formId)
    })
}

function onAfterShow(event) {
    if (event.target !== dialogRef.value) return
    // With a subject already proposed, the message is what is left to write.
    if (title.value) textInputRef.value?.focus()
    else titleInputRef.value?.focus()
}
</script>

<template>
    <wa-dialog
        ref="dialogRef" :open="open"
        label="Send a message to a peer"
        style="--width: min(560px, calc(100vw - 2rem))"
        @wa-show="onShow"
        @wa-after-show="onAfterShow"
        @wa-hide="onHide"
    >
        <form :id="formId" class="pc-form" @submit.prevent="handleSend">
            <p v-if="isReply" class="pc-reply">
                <span class="pc-reply__label">In reply to their</span>
                <span v-if="replyToTitle" class="pc-reply__title">“{{ replyToTitle }}”</span>
            </p>
            <!-- The answered message's fate rides with the reply. Radios: three
                 exclusive choices, all visible — the default is the one that
                 changes nothing. -->
            <wa-radio-group
                v-if="canResolveAnswered"
                size="small" label="The message you are answering"
                :value="resolution" :disabled="busy"
                @change="resolution = $event.target.value"
            >
                <wa-radio value="open">Keep it open — it still awaits your decision</wa-radio>
                <wa-radio value="done">Mark it done — you dealt with it yourself, no agent needed</wa-radio>
                <wa-radio value="refused">Refuse it</wa-radio>
            </wa-radio-group>

            <wa-select
                v-model="selectedPeerId" size="small" label="Peer"
                :disabled="isReply || busy"
            >
                <wa-option
                    v-for="peer in activePeers" :key="peer.id"
                    :value="peer.id" :label="peersStore.peerLabel(peer.id)"
                >{{ peersStore.peerLabel(peer.id) }}</wa-option>
            </wa-select>
            <p v-if="!activePeers.length" class="pc-hint">No active peer to write to.</p>

            <wa-input
                ref="titleInputRef"
                size="small" label="Title" :maxlength="PEER_MESSAGE_TITLE_MAX_CHARS"
                placeholder="One line, like an email subject"
                :value="title" :disabled="busy"
                @input="title = $event.target.value"
            ></wa-input>

            <wa-textarea
                ref="textInputRef"
                size="small" label="Message" rows="6"
                placeholder="Sent exactly as written — the receiving side sees it comes directly from you"
                :value="text" :disabled="busy"
                @input="text = $event.target.value"
            ></wa-textarea>

            <p class="pc-hint">
                Text only, sent in your name — no attachments, and nothing is kept if
                you close. For attachments, or to have the message composed for you,
                ask an agent to send it.
            </p>

            <wa-callout v-if="error" variant="danger" size="small">{{ error }}</wa-callout>
            <wa-callout v-if="confirmingDiscard" variant="warning" size="small">
                <div class="pc-confirm-body">
                    <span>Discard this message? Nothing is saved.</span>
                    <span class="pc-confirm__actions">
                        <wa-button size="small" variant="danger" @click="discard">Discard</wa-button>
                        <wa-button size="small" appearance="outlined" @click="confirmingDiscard = false">Keep writing</wa-button>
                    </span>
                </div>
            </wa-callout>
        </form>

        <div slot="footer" class="pc-footer">
            <wa-button :disabled="busy" @click="attemptClose">Cancel</wa-button>
            <wa-button
                ref="sendButtonRef"
                variant="brand" type="submit"
                :disabled="busy || !activePeers.length"
                :loading="busy"
            >
                <wa-icon name="paper-plane" slot="start"></wa-icon>
                Send
            </wa-button>
        </div>
    </wa-dialog>
</template>

<style scoped>
.pc-form {
    display: flex;
    flex-direction: column;
    gap: var(--wa-space-s);
}
.pc-reply {
    display: flex;
    align-items: baseline;
    gap: var(--wa-space-2xs);
    margin: 0;
    font-size: var(--wa-font-size-s);
}
.pc-reply__label { color: var(--wa-color-text-quiet); }
.pc-reply__title { font-weight: var(--wa-font-weight-semibold); }
.pc-hint {
    margin: 0;
    font-size: var(--wa-font-size-xs);
    color: var(--wa-color-text-quiet);
}
/* Confirmation callouts: breathing room between the text and the buttons,
   and between the buttons themselves (PeersManagerDialog's recipe). */
.pc-confirm-body {
    display: flex;
    flex-direction: column;
    gap: var(--wa-space-s);
}
.pc-confirm__actions {
    display: flex;
    gap: var(--wa-space-s);
}
.pc-footer {
    display: flex;
    justify-content: flex-end;
    gap: var(--wa-space-s);
    width: 100%;
}
</style>
