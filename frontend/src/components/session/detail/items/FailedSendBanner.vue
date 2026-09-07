<script setup>
// Failure banner shown under a failed-send bubble (messaging pattern: an
// undeliverable message stays in the conversation flow, marked failed, with
// Retry / Edit / Delete actions). Provider-agnostic — the bubble itself is
// rendered by the provider's user-message renderer; this banner only reads
// the synthetic parsed content's ``failedSend`` field and the store entry.

import { computed, inject } from 'vue'
import { useDataStore } from '../../../../stores/data'
import { sendWsMessage } from '../../../../composables/useWebSocket'
import { generateUUID } from '../../../../utils/crypto'
import { mediasToSdkFormat } from '../../../../utils/fileUtils'

const props = defineProps({
    // Parsed content of the synthetic item (carries ``failedSend``)
    content: {
        type: Object,
        required: true
    },
    projectId: {
        type: String,
        required: true
    },
    sessionId: {
        type: String,
        required: true
    }
})

const store = useDataStore()
const insertTextAtCursor = inject('insertTextAtCursor', null)

const failedSend = computed(() => props.content?.failedSend || null)

// A hybrid send rejected because the CLI composer was busy (a TUI dialog was
// open, or the user had text typed) carries a now-or-never reason. The backend
// polls every 2.5s and clears ``terminal_blocked`` once the composer is ready
// again — at which point the original "resolve the dialog" wording is stale and
// confusing. Detect that positive unblock (strict ``=== false``: a hybrid agent
// always reports the flag, so this is real knowledge, not "no state") and swap
// the message for a ready-to-retry one. Purely presentational; the user still
// drives the actual resend with Retry.
const composerReadyAgain = computed(() =>
    failedSend.value?.code === 'hybrid_composer_busy'
    && store.getProcessState(props.sessionId)?.extra?.terminal_blocked === false
)

const displayMessage = computed(() =>
    composerReadyAgain.value
        ? 'The Claude CLI terminal was blocked and is ready again — retry to send your message.'
        : (failedSend.value?.message || '')
)

// A message made only of attachments whose attachments could not be preserved
// (too large for IndexedDB, dropped on the reload that rediscovered it) has
// nothing left to send: Retry and Edit would both be no-ops. Only Delete stays.
const nothingLeftToSend = computed(() =>
    !!failedSend.value?.mediasDropped && !(failedSend.value?.text || '').trim()
)

function getEntry() {
    const requestId = failedSend.value?.requestId
    return requestId ? store.getFailedSend(props.sessionId, requestId) : null
}

/**
 * Re-send the message as-is. The payload re-sends the session's CURRENT
 * settings untouched: the backend overwrites the stored settings bundle with
 * whatever the payload carries, so omitting them would reset the session's
 * forced settings to "use global default".
 */
function retry() {
    const entry = getEntry()
    if (!entry) return
    const session = store.getSession(props.sessionId)
    const requestId = generateUUID()
    const { images, documents } = mediasToSdkFormat(entry.medias || [])
    const payload = {
        type: 'send_message',
        session_id: props.sessionId,
        project_id: props.projectId,
        provider: session?.provider,
        text: entry.text,
        permission_mode: session?.permission_mode ?? null,
        selected_model: session?.selected_model ?? null,
        effort: session?.effort ?? null,
        thinking_enabled: session?.thinking_enabled ?? null,
        claude_in_chrome: session?.claude_in_chrome ?? null,
        fast_mode: session?.fast_mode ?? null,
        context_max: session?.context_max ?? null,
        request_id: requestId,
    }
    if (images.length) payload.images = images
    if (documents.length) payload.documents = documents
    store.applyCreationSendMode(payload)
    // WebSocket down: keep the failed bubble, the user can retry later
    if (!sendWsMessage(payload)) return
    store.registerOutgoingSend(props.sessionId, props.projectId, requestId, {
        text: entry.text,
        medias: entry.medias || [],
        images,
        documents,
    })
    store.removeFailedSend(props.sessionId, entry.requestId)
}

/** Put the message back into the composer for rework, then drop the bubble. */
async function edit() {
    const entry = getEntry()
    if (!entry || !insertTextAtCursor) return
    insertTextAtCursor(entry.text)
    // insertTextAtCursor only focuses when the composer is already expanded; a
    // collapsed composer just gets the text appended to its draft and stays put
    // (so reading + commenting never pops it open). But Edit is an explicit "I
    // want to rework this now", so open + focus the composer. Mirror the "Expand
    // Message Input" command: dispatch twicc:expand-composer on the collapsed
    // composer — MessageInput expands it, reduces any pending request, and
    // focuses the textarea itself. (Not focusChatPrimary, which would steer focus
    // to a pending request instead of the composer.)
    document
        .querySelector('.message-input.collapsed')
        ?.dispatchEvent(new CustomEvent('twicc:expand-composer'))
    if (entry.medias?.length) {
        await store.restoreDraftAttachments(props.sessionId, entry.medias)
    }
    store.removeFailedSend(props.sessionId, entry.requestId)
}

function discard() {
    const entry = getEntry()
    if (!entry) return
    store.removeFailedSend(props.sessionId, entry.requestId)
}
</script>

<template>
    <!-- Reuse the familiar danger callout (same idiom as ApiError) for the
         undeliverable-message notice + its Retry / Edit / Delete actions. -->
    <wa-callout
        v-if="failedSend"
        variant="danger"
        appearance="outlined"
        size="small"
        class="failed-send-callout"
    >
        <wa-icon slot="icon" name="circle-exclamation"></wa-icon>
        <div class="failed-send-content">
            <div class="failed-send-message">{{ displayMessage }}</div>
            <div v-if="nothingLeftToSend" class="failed-send-note">
                Its attachments were too large to preserve, and it carried no text — nothing is left to retry.
            </div>
            <div v-else-if="failedSend.mediasDropped" class="failed-send-note">
                Its attachments were too large to preserve — only the text can be retried or edited.
            </div>
            <div class="failed-send-actions">
                <wa-button
                    size="small"
                    variant="danger"
                    appearance="outlined"
                    :disabled="nothingLeftToSend"
                    @click="retry"
                >
                    <wa-icon slot="start" name="rotate-right"></wa-icon>
                    Retry
                </wa-button>
                <wa-button
                    v-if="insertTextAtCursor"
                    size="small"
                    variant="neutral"
                    appearance="outlined"
                    :disabled="nothingLeftToSend"
                    @click="edit"
                >
                    <wa-icon slot="start" name="pen"></wa-icon>
                    Edit
                </wa-button>
                <wa-button size="small" variant="neutral" appearance="plain" @click="discard">
                    Delete
                </wa-button>
            </div>
        </div>
    </wa-callout>
</template>

<style scoped>
.failed-send-callout {
    margin-top: var(--wa-space-s);
}

.failed-send-content {
    display: flex;
    flex-direction: column;
    gap: var(--wa-space-m);
}

.failed-send-note {
    font-size: var(--wa-font-size-s);
    font-weight: var(--wa-font-weight-semibold);
}

.failed-send-actions {
    display: flex;
    align-items: center;
    justify-content: flex-end;
    flex-wrap: wrap;
    gap: var(--wa-space-xs);
}
</style>
