<script setup>
// MessageInput.vue - Text input for sending messages to Claude
import { ref, computed, watch, onMounted, nextTick } from 'vue'
import { useRouter } from 'vue-router'
import { useDataStore } from '../stores/data'
import { sendWsMessage } from '../composables/useWebSocket'
import { useVisualViewport } from '../composables/useVisualViewport'

// Track visual viewport height for mobile keyboard handling
useVisualViewport()

const props = defineProps({
    sessionId: {
        type: String,
        required: true
    },
    projectId: {
        type: String,
        required: true
    }
})

const router = useRouter()
const store = useDataStore()

// Get session data to check if it's a draft
const session = computed(() => store.getSession(props.sessionId))
const isDraft = computed(() => session.value?.draft === true)

// Local state for the textarea
const messageText = ref('')
const textareaRef = ref(null)

// Get process state for this session
const processState = computed(() => store.getProcessState(props.sessionId))

// Determine if input/button should be disabled
const isDisabled = computed(() => {
    const state = processState.value?.state
    // Disabled during starting and assistant_turn
    return state === 'starting' || state === 'assistant_turn'
})

// Button label based on process state
const buttonLabel = computed(() => {
    const state = processState.value?.state
    if (state === 'starting') {
        return 'Starting...'
    }
    if (state === 'assistant_turn') {
        return 'Claude is working...'
    }
    // user_turn, dead, or no process
    return 'Send'
})

// Placeholder text based on process state
const placeholderText = computed(() => {
    const state = processState.value?.state
    if (state === 'starting') {
        return 'Starting Claude process...'
    }
    if (state === 'assistant_turn') {
        return 'Waiting for Claude to respond...'
    }
    // user_turn, dead, or no process
    return 'Type your message...'
})

// Restore draft message when session changes
watch(() => props.sessionId, (newId) => {
    const draft = store.getDraftMessage(newId)
    messageText.value = draft?.message || ''
}, { immediate: true })

// Save draft message on each keystroke (debounced in store)
watch(messageText, (newText) => {
    store.setDraftMessage(props.sessionId, newText)
})

// Autofocus textarea for draft sessions
onMounted(async () => {
    if (isDraft.value && textareaRef.value) {
        // Wait for next tick to ensure the component is fully rendered
        await nextTick()
        textareaRef.value.focus()
    }
})

/**
 * Handle textarea input event.
 */
function onInput(event) {
    messageText.value = event.target.value
}

/**
 * Handle keyboard shortcuts in textarea.
 * Cmd/Ctrl+Enter submits the message.
 */
function onKeydown(event) {
    if ((event.metaKey || event.ctrlKey) && event.key === 'Enter') {
        event.preventDefault()
        handleSend()
    }
}

/**
 * Send the message via WebSocket.
 * Backend handles both new and existing sessions with the same message type.
 * For draft sessions with a custom title, include the title in the message.
 */
function handleSend() {
    const text = messageText.value.trim()
    if (!text || isDisabled.value) return

    // Build the message payload
    const payload = {
        type: 'send_message',
        session_id: props.sessionId,
        project_id: props.projectId,
        text: text
    }

    // For draft sessions with a custom title (not "New session"), include it
    if (isDraft.value && session.value?.title && session.value.title !== 'New session') {
        payload.title = session.value.title
    }

    const success = sendWsMessage(payload)

    if (success) {
        // Clear draft message from store (and IndexedDB)
        store.clearDraftMessage(props.sessionId)

        // Clear draft session from IndexedDB (if this was a draft session)
        // Note: The session in the store will be replaced by the real session from backend
        if (isDraft.value) {
            store.deleteDraftSession(props.sessionId)
        }

        // Clear the textarea on successful send
        messageText.value = ''
        if (textareaRef.value) {
            textareaRef.value.value = ''
        }
    }
}

/**
 * Cancel the draft session and navigate back to project.
 */
function handleCancel() {
    // Clear draft message from store and IndexedDB
    store.clearDraftMessage(props.sessionId)
    store.deleteDraftSession(props.sessionId)
    router.push({ name: 'project', params: { projectId: props.projectId } })
}
</script>

<template>
    <div class="message-input">
        <wa-textarea
            ref="textareaRef"
            :value.prop="messageText"
            :placeholder="placeholderText"
            rows="3"
            resize="auto"
            @input="onInput"
            @keydown="onKeydown"
        ></wa-textarea>
        <div class="message-input-actions">
            <!-- Cancel button for draft sessions -->
            <wa-button
                v-if="isDraft"
                variant="neutral"
                appearance="outlined"
                @click="handleCancel"
            >
                Cancel
            </wa-button>
            <wa-button
                variant="brand"
                :disabled="isDisabled || !messageText.trim()"
                @click="handleSend"
            >
                <wa-spinner v-if="processState?.state === 'starting' || processState?.state === 'assistant_turn'" slot="prefix"></wa-spinner>
                {{ buttonLabel }}
            </wa-button>
        </div>
    </div>
</template>

<style scoped>
.message-input {
    display: flex;
    flex-direction: column;
    gap: var(--wa-space-s);
    padding: var(--wa-space-s);
    background: var(--wa-color-surface-raised);
    border-top: 1px solid var(--wa-color-border-normal);
}

.message-input wa-textarea::part(textarea) {
    /* Limit height to 40% of visual viewport (accounts for mobile keyboard) */
    max-height: calc(var(--visual-viewport-height, 100dvh) * 0.4);
    /* Override resize="auto" which sets overflow-y: hidden - we need scrolling when max-height is reached */
    overflow-y: auto;
}

.message-input-actions {
    display: flex;
    justify-content: flex-end;
    gap: var(--wa-space-s);
}
</style>
