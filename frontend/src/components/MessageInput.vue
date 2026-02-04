<script setup>
// MessageInput.vue - Text input for sending messages to Claude
import { ref, computed, watch, nextTick } from 'vue'
import { useRouter, useRoute } from 'vue-router'
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
const route = useRoute()
const store = useDataStore()

// Detect "All Projects" mode from route name
const isAllProjectsMode = computed(() =>
    route.name === 'projects-session' ||
    route.name === 'projects-session-subagent'
)

const emit = defineEmits(['needs-title'])

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
    // Disabled only during starting - we allow sending during assistant_turn
    // (Claude Agent SDK supports receiving messages while responding)
    return state === 'starting'
})

// Button label based on process state
const buttonLabel = computed(() => {
    const state = processState.value?.state
    if (state === 'starting') {
        return 'Claude is starting...'
    }
    if (state === 'assistant_turn') {
        return 'Send while Claude is working'
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
        return 'You can send a message now. Claude will receive it as soon as possible (while working or after). Note: it will not appear in the conversation history.'
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

// Autofocus textarea for draft sessions (only once)
const hasAutoFocused = ref(false)

// Watch both isDraft and textareaRef - focus when both are ready
watch([isDraft, textareaRef], async ([isDraftSession, textarea]) => {
    if (isDraftSession && !hasAutoFocused.value && textarea) {
        hasAutoFocused.value = true
        // Wait for Vue's next tick
        await nextTick()
        // Wait for the Web Component to be fully rendered (Lit's updateComplete)
        if (textarea.updateComplete) {
            await textarea.updateComplete
        }
        // Wait until the textarea is visible (offsetParent !== null).
        // When creating a new session from an empty state (no session was selected),
        // the parent components (SessionView, SessionItemsList) are mounted for the first time,
        // and the textarea may not be visible yet. An element with offsetParent === null
        // cannot receive focus.
        const maxAttempts = 20
        for (let i = 0; i < maxAttempts; i++) {
            if (textarea.offsetParent !== null) {
                break
            }
            await new Promise(resolve => requestAnimationFrame(resolve))
        }
        textarea.focus()
    }
}, { immediate: true })

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
 * For draft sessions without a title, send the message AND open the rename dialog.
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

    // For draft sessions with a title, include it
    if (isDraft.value && session.value?.title) {
        payload.title = session.value.title
    }

    // For draft sessions without a title, open the rename dialog (non-blocking)
    // The message is still sent, allowing the agent to start working
    if (isDraft.value && !session.value?.title) {
        emit('needs-title')
    }

    const success = sendWsMessage(payload)

    if (success) {
        // Clear draft message from store (and IndexedDB)
        store.clearDraftMessage(props.sessionId)

        // Clear draft session from IndexedDB only (if this was a draft session)
        // Keep in store so session stays visible until backend confirms with session_added/updated
        if (isDraft.value) {
            store.deleteDraftSession(props.sessionId, { keepInStore: true })
        }

        // Clear the textarea on successful send
        messageText.value = ''
        if (textareaRef.value) {
            textareaRef.value.value = ''
        }
    }
}

/**
 * Cancel the draft session and navigate back to project list.
 * Navigates to 'projects-all' if in All Projects mode, otherwise to 'project'.
 */
function handleCancel() {
    // Clear draft message from store and IndexedDB
    store.clearDraftMessage(props.sessionId)
    store.deleteDraftSession(props.sessionId)

    if (isAllProjectsMode.value) {
        router.push({ name: 'projects-all' })
    } else {
        router.push({ name: 'project', params: { projectId: props.projectId } })
    }
}

/**
 * Clear the textarea content for existing sessions.
 */
function handleClear() {
    messageText.value = ''
    store.clearDraftMessage(props.sessionId)
    if (textareaRef.value) {
        textareaRef.value.value = ''
    }
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
            <!-- Clear button for existing sessions when there's text -->
            <wa-button
                v-else-if="messageText.trim()"
                variant="neutral"
                appearance="outlined"
                @click="handleClear"
            >
                Clear
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
    background: var(--main-header-footer-bg-color);
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
