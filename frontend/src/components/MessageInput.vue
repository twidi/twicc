<script setup>
// MessageInput.vue - Text input for sending messages to Claude
import { ref, computed, watch, nextTick, useId } from 'vue'
import { useRouter, useRoute } from 'vue-router'
import { useDataStore } from '../stores/data'
import { useSettingsStore } from '../stores/settings'
import { sendWsMessage, notifyUserDraftUpdated } from '../composables/useWebSocket'
import { useVisualViewport } from '../composables/useVisualViewport'
import { isSupportedMimeType, MAX_FILE_SIZE, SUPPORTED_IMAGE_TYPES, draftMediaToMediaItem } from '../utils/fileUtils'
import { toast } from '../composables/useToast'
import { PERMISSION_MODE, PERMISSION_MODE_LABELS, PERMISSION_MODE_DESCRIPTIONS, PERMISSION_MODE_ICONS, PERMISSION_MODE_COLORS, MODEL, MODEL_LABELS, MODEL_ICONS, MODEL_COLORS, EFFORT, EFFORT_LABELS, EFFORT_DISPLAY_LABELS, EFFORT_ICONS, EFFORT_COLORS, THINKING, THINKING_DISPLAY_LABELS, THINKING_ICONS, THINKING_COLORS } from '../constants'
import MediaThumbnailGroup from './MediaThumbnailGroup.vue'
import AppTooltip from './AppTooltip.vue'

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
const settingsStore = useSettingsStore()

// Detect "All Projects" mode from route name
const isAllProjectsMode = computed(() => route.name?.startsWith('projects-'))

const emit = defineEmits(['needs-title'])

// Get session data to check if it's a draft
const session = computed(() => store.getSession(props.sessionId))
const isDraft = computed(() => session.value?.draft === true)

// Local state for the textarea
const messageText = ref('')
const textareaRef = ref(null)
const fileInputRef = ref(null)
const attachButtonId = useId()
const modelSelectId = useId()
const effortSelectId = useId()
const thinkingSelectId = useId()
const permissionSelectId = useId()

// Attachments for this session
const attachments = computed(() => store.getAttachments(props.sessionId))
const attachmentCount = computed(() => store.getAttachmentCount(props.sessionId))

// Convert DraftMedia objects to normalized MediaItem format for the thumbnail group
const mediaItems = computed(() => attachments.value.map(a => draftMediaToMediaItem(a)))

// Permission mode options for the dropdown
const permissionModeOptions = Object.values(PERMISSION_MODE).map(value => ({
    value,
    label: PERMISSION_MODE_LABELS[value],
    description: PERMISSION_MODE_DESCRIPTIONS[value],
    icon: PERMISSION_MODE_ICONS[value],
    color: PERMISSION_MODE_COLORS[value],
}))

// Model options for the dropdown
const modelOptions = Object.values(MODEL).map(value => ({
    value,
    label: MODEL_LABELS[value],
    icon: MODEL_ICONS[value],
    color: MODEL_COLORS[value],
}))

// Effort options for the dropdown
const effortOptions = Object.values(EFFORT).map(value => ({
    value,
    label: EFFORT_LABELS[value],
    displayLabel: EFFORT_DISPLAY_LABELS[value],
    icon: EFFORT_ICONS[value],
    color: EFFORT_COLORS[value],
}))

// Thinking options for the dropdown (use string values for wa-select compatibility)
const thinkingOptions = [
    { value: 'true', label: THINKING_DISPLAY_LABELS[true], icon: THINKING_ICONS[true], color: THINKING_COLORS[true] },
    { value: 'false', label: THINKING_DISPLAY_LABELS[false], icon: THINKING_ICONS[false], color: THINKING_COLORS[false] },
]

// Dynamic icon and color for the selected model (shown in the select's start slot)
const selectedModelIcon = computed(() => MODEL_ICONS[selectedModel.value] || 'star')
const selectedModelColor = computed(() => MODEL_COLORS[selectedModel.value] || 'inherit')

// Dynamic icon and color for the selected permission mode (shown in the select's start slot)
const selectedPermissionIcon = computed(() => PERMISSION_MODE_ICONS[selectedPermissionMode.value] || 'shield-halved')
const selectedPermissionColor = computed(() => PERMISSION_MODE_COLORS[selectedPermissionMode.value] || 'inherit')

// Dynamic icon and color for the selected effort (shown in the select's start slot)
const selectedEffortIcon = computed(() => EFFORT_ICONS[selectedEffort.value] || 'gauge')
const selectedEffortColor = computed(() => EFFORT_COLORS[selectedEffort.value] || 'inherit')

// Dynamic icon and color for the selected thinking mode (shown in the select's start slot)
const selectedThinkingIcon = computed(() => THINKING_ICONS[selectedThinking.value] || 'brain')
const selectedThinkingColor = computed(() => THINKING_COLORS[selectedThinking.value] || 'inherit')

// Selected permission mode for the current session
const selectedPermissionMode = ref('default')

// Selected model for the current session
const selectedModel = ref('opus')

// Selected effort level for the current session
const selectedEffort = ref('medium')

// Selected thinking mode for the current session
const selectedThinking = ref(true)

// Get process state for this session
const processState = computed(() => store.getProcessState(props.sessionId))

// Whether files are currently being processed (encoded/resized) for this session
const isProcessingFiles = computed(() => store.isProcessingAttachments(props.sessionId))

// Determine if input/button should be disabled
const isDisabled = computed(() => {
    // Cannot send without a WebSocket connection
    if (!store.wsConnected) return true
    // Cannot send while the initial sync is running (sessions not available yet)
    if (store.isInitialSyncInProgress) return true
    // Cannot send while files are being processed (encoding, resizing)
    if (isProcessingFiles.value) return true
    // Disabled during process startup - we allow sending during assistant_turn
    // (Claude Agent SDK supports receiving messages while responding)
    const state = processState.value?.state
    return state === 'starting'
})

// Model dropdown is disabled during starting and assistant_turn.
// The SDK's set_model() does not work on a live process, so model can only
// be changed during user_turn (sent with the next message) or when no process runs.
const isModelDisabled = computed(() => {
    const state = processState.value?.state
    return state === 'starting' || state === 'assistant_turn'
})

// Effort and thinking dropdowns are disabled whenever a process is running.
// These cannot be changed on a live SDK client — they are only set at process creation.
const isEffortThinkingDisabled = computed(() => {
    const state = processState.value?.state
    return state === 'starting' || state === 'assistant_turn' || state === 'user_turn'
})

// Button label based on process state and whether this is a settings-only update
const buttonLabel = computed(() => {
    const state = processState.value?.state
    if (state === 'starting') {
        return 'Starting...'
    }
    // When settings changed and no text, show what will be updated
    if (hasSettingsChanged.value && !messageText.value.trim()) {
        if (hasModelChanged.value && hasPermissionChanged.value) {
            return 'Update model & permissions'
        }
        if (hasModelChanged.value) {
            return 'Update model'
        }
        return 'Update permissions'
    }
    return 'Send'
})

// Button icon changes based on mode (settings-only update vs regular send)
const buttonIcon = computed(() => {
    if (hasSettingsChanged.value && !messageText.value.trim()) {
        return 'arrows-rotate'
    }
    return 'paper-plane'
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

// Whether a process is actively running (not starting, not dead)
const processIsActive = computed(() => {
    const state = processState.value?.state
    return state === 'assistant_turn' || state === 'user_turn'
})

// Track the "active" values currently applied on the live SDK process.
// Also serve as reference values for Reset: initialized to resolved defaults
// so that dropdown change detection works even when no process is active.
const activeModel = ref(null)
const activePermissionMode = ref(null)
const activeEffort = ref(null)
const activeThinking = ref(null)

// Detect whether the user has changed the dropdowns from their reference values.
// This works regardless of process state (used for Reset button visibility).
const hasDropdownsChanged = computed(() =>
    selectedModel.value !== activeModel.value ||
    selectedPermissionMode.value !== activePermissionMode.value ||
    selectedEffort.value !== activeEffort.value ||
    selectedThinking.value !== activeThinking.value
)

// Detect dropdown changes on an active process (used for Send button "Update" mode).
// Only meaningful when a process is running, since updating settings via SDK
// requires an active process.
const hasModelChanged = computed(() =>
    processIsActive.value && selectedModel.value !== activeModel.value
)
const hasPermissionChanged = computed(() =>
    processIsActive.value && selectedPermissionMode.value !== activePermissionMode.value
)
const hasSettingsChanged = computed(() => hasModelChanged.value || hasPermissionChanged.value)

// Determine the effective permission mode for the current session.
// If "always apply default" is on, the settings default wins regardless of DB value.
// Otherwise, use the DB value if present, or fall back to settings default.
function resolvePermissionMode(sess) {
    if (settingsStore.isAlwaysApplyDefaultPermissionMode) {
        return settingsStore.getDefaultPermissionMode
    }
    return sess?.permission_mode || settingsStore.getDefaultPermissionMode
}

// Determine the effective model for the current session.
// Same logic as permission mode: "always apply" overrides, otherwise DB or settings default.
function resolveModel(sess) {
    if (settingsStore.isAlwaysApplyDefaultModel) {
        return settingsStore.getDefaultModel
    }
    return sess?.selected_model || settingsStore.getDefaultModel
}

// Determine the effective effort for the current session.
function resolveEffort(sess) {
    if (settingsStore.isAlwaysApplyDefaultEffort) {
        return settingsStore.getDefaultEffort
    }
    return sess?.effort || settingsStore.getDefaultEffort
}

// Determine the effective thinking mode for the current session.
function resolveThinking(sess) {
    if (settingsStore.isAlwaysApplyDefaultThinking) {
        return settingsStore.getDefaultThinking
    }
    return sess?.thinking_enabled ?? settingsStore.getDefaultThinking
}

// Sync permission mode and model when session changes
watch(() => props.sessionId, (newId) => {
    const sess = store.getSession(newId)
    const resolvedPermission = resolvePermissionMode(sess)
    const resolvedModel = resolveModel(sess)
    const resolvedEffort = resolveEffort(sess)
    const resolvedThinking = resolveThinking(sess)
    selectedPermissionMode.value = resolvedPermission
    selectedModel.value = resolvedModel
    selectedEffort.value = resolvedEffort
    selectedThinking.value = resolvedThinking
    // Initialize active values from the session, falling back to resolved defaults
    // so that dropdown change detection works even when no process is active.
    activePermissionMode.value = sess?.permission_mode || resolvedPermission
    activeModel.value = sess?.selected_model || resolvedModel
    activeEffort.value = sess?.effort || resolvedEffort
    activeThinking.value = sess?.thinking_enabled ?? resolvedThinking
}, { immediate: true })

// When the default permission mode setting changes, or the "always apply" toggle
// changes, update the dropdown for sessions that should follow the default.
// Don't overwrite user's selection when a process is active (they may be changing it intentionally).
// Also update the active reference value so Reset detection stays in sync.
watch(
    () => [settingsStore.getDefaultPermissionMode, settingsStore.isAlwaysApplyDefaultPermissionMode],
    () => {
        if (processIsActive.value) return
        const sess = store.getSession(props.sessionId)
        const resolved = resolvePermissionMode(sess)
        selectedPermissionMode.value = resolved
        activePermissionMode.value = resolved
    }
)

// When the default model setting changes, or the "always apply" toggle
// changes, update the dropdown for sessions that should follow the default.
// Don't overwrite user's selection when a process is active (they may be changing it intentionally).
// Also update the active reference value so Reset detection stays in sync.
watch(
    () => [settingsStore.getDefaultModel, settingsStore.isAlwaysApplyDefaultModel],
    () => {
        if (processIsActive.value) return
        const sess = store.getSession(props.sessionId)
        const resolved = resolveModel(sess)
        selectedModel.value = resolved
        activeModel.value = resolved
    }
)

// When the default effort setting changes, update the dropdown for sessions that should follow the default.
watch(
    () => [settingsStore.getDefaultEffort, settingsStore.isAlwaysApplyDefaultEffort],
    () => {
        if (processIsActive.value) return
        const sess = store.getSession(props.sessionId)
        const resolved = resolveEffort(sess)
        selectedEffort.value = resolved
        activeEffort.value = resolved
    }
)

// When the default thinking setting changes, update the dropdown for sessions that should follow the default.
watch(
    () => [settingsStore.getDefaultThinking, settingsStore.isAlwaysApplyDefaultThinking],
    () => {
        if (processIsActive.value) return
        const sess = store.getSession(props.sessionId)
        const resolved = resolveThinking(sess)
        selectedThinking.value = resolved
        activeThinking.value = resolved
    }
)

// Also react when session data arrives from backend (e.g., after watcher creates the row).
// Don't overwrite user's selection when a process is active.
// Always update the active values to track what the process is currently using.
watch(
    () => store.getSession(props.sessionId)?.permission_mode,
    (newMode) => {
        if (newMode) {
            activePermissionMode.value = newMode
            if (!processIsActive.value) {
                selectedPermissionMode.value = newMode
            }
        }
    }
)

// React when selected_model data arrives from backend.
// Don't overwrite user's selection when a process is active.
// Always update the active values to track what the process is currently using.
watch(
    () => store.getSession(props.sessionId)?.selected_model,
    (newModel) => {
        if (newModel) {
            activeModel.value = newModel
            if (!processIsActive.value) {
                selectedModel.value = newModel
            }
        }
    }
)

// React when effort data arrives from backend.
watch(
    () => store.getSession(props.sessionId)?.effort,
    (newEffort) => {
        if (newEffort) {
            activeEffort.value = newEffort
            if (!processIsActive.value) {
                selectedEffort.value = newEffort
            }
        }
    }
)

// React when thinking_enabled data arrives from backend.
watch(
    () => store.getSession(props.sessionId)?.thinking_enabled,
    (newThinking) => {
        if (newThinking != null) {
            activeThinking.value = newThinking
            if (!processIsActive.value) {
                selectedThinking.value = newThinking
            }
        }
    }
)

// Restore draft message when session changes
watch(() => props.sessionId, async (newId) => {
    const draft = store.getDraftMessage(newId)
    messageText.value = draft?.message || ''
    // Adjust textarea height after the DOM updates with restored content
    await nextTick()
    if (textareaRef.value?.updateComplete) {
        await textareaRef.value.updateComplete
    }
    adjustTextareaHeight()
}, { immediate: true })

// Also restore draft when it arrives after hydration (initial page load)
// This handles the race condition where the component mounts before IndexedDB is loaded
watch(
    () => store.getDraftMessage(props.sessionId),
    async (draft) => {
        // Only restore if textarea is still empty (don't overwrite user typing)
        if (!messageText.value && draft?.message) {
            messageText.value = draft.message
            // Adjust textarea height after the DOM updates with restored content
            await nextTick()
            if (textareaRef.value?.updateComplete) {
                await textareaRef.value.updateComplete
            }
            adjustTextareaHeight()
        }
    }
)

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
        adjustTextareaHeight()
        textarea.focus()
    }
}, { immediate: true })

/**
 * Adjust the textarea height to fit its content.
 * Accesses the internal <textarea> inside the wa-textarea shadow DOM
 * to perform a single synchronous height reset + scrollHeight read.
 * Unlike wa-textarea's built-in resize="auto", this avoids the
 * ResizeObserver feedback loop that causes 1px jitter.
 */
function adjustTextareaHeight() {
    const textarea = textareaRef.value?.shadowRoot?.querySelector('textarea')
    if (!textarea) return
    // Reset to auto to measure natural scrollHeight
    textarea.style.height = 'auto'
    // Only set an explicit height if content exceeds the natural rows height.
    // When scrollHeight <= the natural height (determined by the rows="3" attribute),
    // leaving height as "auto" lets the browser use the rows attribute as the floor.
    if (textarea.scrollHeight > textarea.clientHeight) {
        textarea.style.height = `${textarea.scrollHeight}px`
    }
}

/**
 * Handle textarea input event.
 * Also notifies the server that the user is actively drafting (debounced).
 */
function onInput(event) {
    messageText.value = event.target.value
    adjustTextareaHeight()
    // Notify server that user is actively preparing a message (debounced)
    // This prevents auto-stop of the process due to inactivity timeout
    notifyUserDraftUpdated(props.sessionId)
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
 * Handle paste event to capture images from clipboard.
 * Only processes image files from clipboard.
 */
async function onPaste(event) {
    const items = event.clipboardData?.items
    if (!items) return

    for (const item of items) {
        // Only handle image files from clipboard
        if (item.kind === 'file' && SUPPORTED_IMAGE_TYPES.includes(item.type)) {
            const file = item.getAsFile()
            if (file) {
                event.preventDefault()
                await processFile(file)
                return // Process only the first image
            }
        }
    }
}

/**
 * Process and add a file as an attachment.
 */
async function processFile(file) {
    // Validate MIME type
    if (!isSupportedMimeType(file.type)) {
        const extension = file.name.split('.').pop()?.toLowerCase() || 'unknown'
        toast.error(`Unsupported file type: .${extension}`, {
            title: 'Cannot attach file'
        })
        return
    }

    // Validate file size
    if (file.size > MAX_FILE_SIZE) {
        const sizeMB = (file.size / 1024 / 1024).toFixed(1)
        toast.error(`File too large: ${sizeMB} MB (max 5 MB)`, {
            title: 'Cannot attach file'
        })
        return
    }

    try {
        await store.addAttachment(props.sessionId, file)
        // Notify server that user is actively preparing a message
        notifyUserDraftUpdated(props.sessionId)
    } catch (error) {
        toast.error(error.message || 'Failed to process file', {
            title: 'Cannot attach file'
        })
    }
}

/**
 * Open the file picker dialog.
 */
function openFilePicker() {
    fileInputRef.value?.click()
}

/**
 * Handle file selection from the file picker.
 */
async function onFileSelected(event) {
    const files = event.target.files
    if (!files) return

    for (const file of files) {
        await processFile(file)
    }

    // Reset input so the same file can be selected again
    event.target.value = ''
}

/**
 * Remove an attachment by index (from MediaThumbnailGroup).
 * Translates the index back to the DraftMedia id for the store.
 */
function removeAttachmentByIndex(index) {
    const attachment = attachments.value[index]
    if (attachment) {
        store.removeAttachment(props.sessionId, attachment.id)
    }
}

/**
 * Remove all attachments.
 */
function removeAllAttachments() {
    store.clearAttachmentsForSession(props.sessionId)
}

/**
 * Send the message via WebSocket.
 * Backend handles both new and existing sessions with the same message type.
 * For draft sessions with a custom title, include the title in the message.
 * For draft sessions without a title, send the message AND open the rename dialog.
 *
 * Also handles settings-only updates: when text is empty but model/permission
 * mode has changed on an active process, sends a payload with empty text so
 * the backend applies the settings via SDK methods without sending a query.
 */
async function handleSend() {
    const text = messageText.value.trim()
    const isSettingsOnlyUpdate = !text && hasSettingsChanged.value

    // Need either text or settings change to proceed
    if ((!text && !isSettingsOnlyUpdate) || isDisabled.value) return

    // Build the message payload
    const payload = {
        type: 'send_message',
        session_id: props.sessionId,
        project_id: props.projectId,
        text: text,
        permission_mode: selectedPermissionMode.value,
        selected_model: selectedModel.value,
        effort: selectedEffort.value,
        thinking_enabled: selectedThinking.value,
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

    // Include attachments in SDK format if any
    if (attachmentCount.value > 0) {
        const { images, documents } = store.getAttachmentsForSdk(props.sessionId)
        if (images.length > 0) {
            payload.images = images
        }
        if (documents.length > 0) {
            payload.documents = documents
        }
    }

    const success = sendWsMessage(payload)

    if (success) {
        // Sync active values to match what was just sent to the backend.
        // This makes the "Update..." button disappear immediately.
        activeModel.value = selectedModel.value
        activePermissionMode.value = selectedPermissionMode.value
        activeEffort.value = selectedEffort.value
        activeThinking.value = selectedThinking.value

        // For settings-only updates, nothing else to clean up
        if (isSettingsOnlyUpdate) return

        // Show optimistic user message immediately (only when not in assistant_turn,
        // because during assistant_turn the message is queued and the user_message
        // won't arrive until later)
        const state = processState.value?.state
        if (state !== 'assistant_turn') {
            const attachments = (payload.images || payload.documents)
                ? { images: payload.images, documents: payload.documents }
                : undefined
            store.setOptimisticMessage(props.sessionId, text, attachments)

            // Set optimistic STARTING state if no process is running yet.
            // The backend broadcasts STARTING before spawning the subprocess,
            // but the SDK connect() blocks the asyncio event loop, so the
            // WebSocket message only arrives after the subprocess is ready
            // (~2-4 seconds later, alongside ASSISTANT_TURN). This optimistic
            // state gives immediate visual feedback to the user.
            if (!state) {
                store.setProcessState(props.sessionId, props.projectId, 'starting')
            }
        }

        // Clear draft message from store (and IndexedDB)
        store.clearDraftMessage(props.sessionId)

        // Clear attachments from store and IndexedDB
        if (attachmentCount.value > 0) {
            await store.clearAttachmentsForSession(props.sessionId)
        }

        // Clear draft session from IndexedDB only (if this was a draft session)
        // Keep in store so session stays visible until backend confirms with session_updated
        if (isDraft.value) {
            store.deleteDraftSession(props.sessionId, { keepInStore: true })
        }

        // Clear the textarea on successful send.
        // Force-clear the Web Component's value property directly: Vue may skip
        // re-pushing "" via :value.prop if it already pushed "" on a previous send
        // (Vue's template binding deduplicates identical prop values).
        messageText.value = ''
        if (textareaRef.value) {
            // Force-clear both the Web Component property and its internal <textarea>.
            // Setting wa.value alone may be ignored by the Lit setter's dedup check
            // (if _value is already ""), and even when accepted, the Lit re-render
            // with live() can be skipped if Vue's binding already pushed the same value.
            // Directly clearing the inner textarea ensures the DOM is always updated.
            textareaRef.value.value = ''
            const inner = textareaRef.value.shadowRoot?.querySelector('textarea')
            if (inner) inner.value = ''
            await nextTick()
            adjustTextareaHeight()
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
 * Reset the form to its initial state: clear textarea text and
 * restore dropdowns to their active (server-side) values.
 */
async function handleReset() {
    // Clear text if any
    if (messageText.value) {
        messageText.value = ''
        store.clearDraftMessage(props.sessionId)
        if (textareaRef.value) {
            textareaRef.value.value = ''
            const inner = textareaRef.value.shadowRoot?.querySelector('textarea')
            if (inner) inner.value = ''
            await nextTick()
            adjustTextareaHeight()
        }
    }
    // Reset dropdowns to their reference values (active process or resolved defaults)
    if (hasDropdownsChanged.value) {
        if (activeModel.value !== null) {
            selectedModel.value = activeModel.value
        }
        if (activePermissionMode.value !== null) {
            selectedPermissionMode.value = activePermissionMode.value
        }
        if (activeEffort.value !== null) {
            selectedEffort.value = activeEffort.value
        }
        if (activeThinking.value !== null) {
            selectedThinking.value = activeThinking.value
        }
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
            resize="none"
            @input="onInput"
            @keydown="onKeydown"
            @paste="onPaste"
            @focus="adjustTextareaHeight"
        ></wa-textarea>

        <div class="message-input-toolbar">
            <!-- Attachments row: button on left, thumbnails on right -->
            <div class="message-input-attachments">
                <!-- Hidden file input -->
                <input
                    ref="fileInputRef"
                    type="file"
                    multiple
                    accept="image/png,image/jpeg,image/gif,image/webp,application/pdf,text/plain"
                    style="display: none;"
                    @change="onFileSelected"
                />

                <!-- Attach button -->
                <wa-button
                    variant="neutral"
                    appearance="plain"
                    size="small"
                    @click="openFilePicker"
                    :id="attachButtonId"
                >
                    <wa-icon name="paperclip"></wa-icon>
                </wa-button>
                <AppTooltip :for="attachButtonId">Attach files (images, PDF, text)</AppTooltip>

                <!-- Attachment badge + popover -->
                <template v-if="attachmentCount > 0">
                    <button
                        :id="`attachments-popover-trigger-${sessionId}`"
                        class="attachments-badge-trigger"
                    >
                        <wa-badge variant="primary" pill>{{ attachmentCount }}</wa-badge>
                    </button>
                    <AppTooltip :for="`attachments-popover-trigger-${sessionId}`">{{ attachmentCount }} file{{ attachmentCount > 1 ? 's' : '' }} attached</AppTooltip>
                    <wa-popover
                        :for="`attachments-popover-trigger-${sessionId}`"
                        placement="top"
                        class="attachments-popover"
                    >
                        <MediaThumbnailGroup
                            :items="mediaItems"
                            removable
                            @remove="removeAttachmentByIndex"
                        />
                        <div class="popover-actions">
                            <wa-button
                                variant="danger"
                                appearance="outlined"
                                size="small"
                                @click="removeAllAttachments"
                            >
                                <wa-icon name="trash" slot="prefix"></wa-icon>
                                Remove all
                            </wa-button>
                        </div>
                    </wa-popover>
                </template>
            </div>

            <div class="message-input-actions">
                <!-- Model selector -->
                <wa-select
                    :id="modelSelectId"
                    :value.prop="selectedModel"
                    @change="selectedModel = $event.target.value"
                    size="small"
                    class="option-select model-select"
                    :disabled="isModelDisabled"
                    placement="top"
                >
                    <wa-icon
                        slot="start"
                        :name="selectedModelIcon"
                        variant="classic"
                        :style="{ color: selectedModelColor }"
                    ></wa-icon>
                    <wa-option
                        v-for="option in modelOptions"
                        :key="option.value"
                        :value="option.value"
                    >
                        <span class="select-option">
                            <wa-icon :name="option.icon" variant="classic" :style="{ color: option.color }"></wa-icon>
                            <span class="model-label">{{ option.label }}</span>
                        </span>
                    </wa-option>
                </wa-select>
                <AppTooltip :for="modelSelectId">Model selection. Can only be changed on your turn.</AppTooltip>

                <!-- Effort selector -->
                <wa-select
                    :id="effortSelectId"
                    :value.prop="selectedEffort"
                    @change="selectedEffort = $event.target.value"
                    size="small"
                    class="option-select effort-select"
                    :disabled="isEffortThinkingDisabled"
                    placement="top"
                >
                    <wa-icon
                        slot="start"
                        :name="selectedEffortIcon"
                        variant="classic"
                        :style="{ color: selectedEffortColor }"
                    ></wa-icon>
                    <wa-option
                        v-for="option in effortOptions"
                        :key="option.value"
                        :value="option.value"
                        :label="option.displayLabel"
                    >
                        <span class="select-option">
                            <wa-icon :name="option.icon" variant="classic" :style="{ color: option.color }"></wa-icon>
                            <span>{{ option.label }}</span>
                        </span>
                    </wa-option>
                </wa-select>
                <AppTooltip :for="effortSelectId">Effort level. Cannot be changed while a process is running.</AppTooltip>

                <!-- Thinking selector -->
                <wa-select
                    :id="thinkingSelectId"
                    :value.prop="String(selectedThinking)"
                    @change="selectedThinking = $event.target.value === 'true'"
                    size="small"
                    class="option-select thinking-select"
                    :disabled="isEffortThinkingDisabled"
                    placement="top"
                >
                    <wa-icon
                        slot="start"
                        :name="selectedThinkingIcon"
                        variant="classic"
                        :style="{ color: selectedThinkingColor }"
                    ></wa-icon>
                    <wa-option
                        v-for="option in thinkingOptions"
                        :key="option.value"
                        :value="option.value"
                        :label="option.label"
                    >
                        <span class="select-option">
                            <wa-icon :name="option.icon" variant="classic" :style="{ color: option.color }"></wa-icon>
                            <span>{{ option.label }}</span>
                        </span>
                    </wa-option>
                </wa-select>
                <AppTooltip :for="thinkingSelectId">Extended thinking. Cannot be changed while a process is running.</AppTooltip>

                <!-- Permission mode selector -->
                <wa-select
                    :id="permissionSelectId"
                    :value.prop="selectedPermissionMode"
                    @change="selectedPermissionMode = $event.target.value"
                    size="small"
                    class="option-select permission-mode-select"
                    :disabled="isDisabled"
                    placement="top"
                >
                    <wa-icon
                        slot="start"
                        :name="selectedPermissionIcon"
                        variant="classic"
                        :style="{ color: selectedPermissionColor }"
                    ></wa-icon>
                    <wa-option
                        v-for="option in permissionModeOptions"
                        :key="option.value"
                        :value="option.value"
                        :label="option.label"
                    >
                        <span class="select-option">
                            <wa-icon :name="option.icon" variant="classic" :style="{ color: option.color }"></wa-icon>
                            <span>
                                <span>{{ option.label }}</span>
                                <span class="option-description">{{ option.description }}</span>
                            </span>
                        </span>
                    </wa-option>
                </wa-select>
                <AppTooltip :for="permissionSelectId">Permission mode. Can be changed at any time, even while Claude is working.</AppTooltip>

                <!-- Cancel button for draft sessions -->
                <wa-button
                    v-if="isDraft"
                    variant="neutral"
                    appearance="outlined"
                    @click="handleCancel"
                    size="small"
                    class="cancel-button"
                >
                    <wa-icon name="xmark" variant="classic"></wa-icon>
                    <span>Cancel</span>
                </wa-button>
                <!-- Reset button for existing sessions: resets text and/or dropdowns -->
                <wa-button
                    v-else-if="messageText.trim() || hasDropdownsChanged"
                    variant="neutral"
                    appearance="outlined"
                    @click="handleReset"
                    size="small"
                    class="reset-button"
                >
                    <wa-icon name="xmark" variant="classic"></wa-icon>
                    <span>Reset</span>
                </wa-button>
                <!-- Send / Update button: dynamically labeled based on state -->
                <wa-button
                    variant="brand"
                    :disabled="isDisabled || (!messageText.trim() && !hasSettingsChanged)"
                    @click="handleSend"
                    size="small"
                    class="send-button"
                >
                    <wa-icon :name="buttonIcon" variant="classic"></wa-icon>
                    <span>{{ buttonLabel }}</span>
                </wa-button>
            </div>
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
    container: message-input / inline-size;
}

.message-input wa-textarea::part(textarea) {
    /* Limit height to 40% of visual viewport (accounts for mobile keyboard) */
    max-height: calc(var(--visual-viewport-height, 100dvh) * 0.4);
    /* Allow scrolling when content exceeds max-height */
    overflow-y: auto;
}

.message-input-toolbar {
    display: flex;
    align-items: center;
    justify-content: space-between;
    gap: var(--wa-space-s);
    @media (width < 640px) {
        padding-left: 2.75rem;
    }
}

/* When sidebar is closed, the sidebar toggle button overlaps
   the attach button area. Add left padding to make room. */
body.sidebar-closed .message-input-toolbar {
    @media (width >= 640px) {
        padding-left: 3.5rem;
    }
}

.message-input-attachments {
    display: flex;
    align-items: center;
    gap: var(--wa-space-s);
    min-width: 0;
    @media (width < 640px) {
        gap: var(--wa-space-xs);
    }
}

.option-select {
    .select-option {
        display: flex;
        align-items: baseline;
        gap: var(--wa-space-s);
        wa-icon {
            position: relative;
            top: var(--wa-space-3xs);
        }
    }
    .option-description {
        display: block;
        font-size: var(--wa-font-size-s);
        color: var(--wa-color-text-quiet);
    }
    &::part(combobox) {
        padding-inline: var(--wa-space-xs);
    }
    &::part(expand-icon) {
        margin-inline-start: var(--wa-space-2xs);;
    }
    &:deep(> wa-icon)  {
        margin-inline-end: var(--wa-space-2xs);
    }
}

.model-select {
    &::part(display-input) {
        max-width: 3rem;
    }
    &::part(listbox) {
        width: 8rem;
    }
}
.effort-select {
    &::part(display-input) {
        max-width: 3.5rem;
    }
    &::part(listbox) {
        width: 8.5rem;
    }
}
.thinking-select {
    &::part(display-input) {
        max-width: 5rem;
    }
    &::part(listbox) {
        width: 10rem;
    }
}
.permission-mode-select {
    &::part(display-input) {
        max-width: 5.5rem;
    }
    &::part(listbox) {
        width: 16rem;
    }
}

.message-input-actions {
    display: flex;
    gap: var(--wa-space-s);
    flex-shrink: 0;
    flex-wrap: wrap;
    max-width: calc(100% - 7rem);
    justify-content: flex-end;

    .cancel-button, .reset-button, .send-button {
        wa-icon {
            display: none;
        }
        & > span {
            display: inline-block;
        }
    }
}

/* On mobile, only show icons */
@container message-input (width < 35rem) {
    .option-select {
        &::part(display-input) {
            display: none;
        }
        &::part(combobox) {
            padding-inline: var(--wa-space-s);
        }
        &:deep(> wa-icon)  {
            margin-inline-end: 0;
        }
        &::part(expand-icon) {
            display: none;
        }
    }
    .permission-mode-select {
        &::part(listbox) {
            translate: -13.5rem 0;
        }
    }
    .message-input-actions {
        gap: var(--wa-space-2xs);
        max-width: calc(100% - 6rem);

        .cancel-button, .reset-button, .send-button {
            &::part(base) {
                padding-inline: var(--wa-space-s);
            }

            wa-icon {
                display: inline-flex;
            }

            & > span {
                display: none;
            }
        }
    }
}

.attachments-badge-trigger {
    background: none;
    border: none;
    padding: 0;
    cursor: pointer;
    display: flex;
    align-items: center;
    box-shadow: none;
    background: var(--wa-color-brand);
    height: 1.5rem;
    min-width: 1.5rem;
    margin-bottom: 0;
}

.attachments-popover {
    --max-width: min(400px, 90vw);
    --arrow-size: 16px;
}

.popover-actions {
    display: flex;
    justify-content: center;
    margin-top: var(--wa-space-l);
}

</style>
