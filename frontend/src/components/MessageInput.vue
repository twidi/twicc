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
import { PERMISSION_MODE, PERMISSION_MODE_LABELS, PERMISSION_MODE_DESCRIPTIONS, MODEL, MODEL_LABELS, EFFORT, EFFORT_LABELS, EFFORT_DISPLAY_LABELS, THINKING_LABELS, THINKING_DISPLAY_LABELS, CLAUDE_IN_CHROME_LABELS, CLAUDE_IN_CHROME_DISPLAY_LABELS } from '../constants'
import MediaThumbnailGroup from './MediaThumbnailGroup.vue'
import AppTooltip from './AppTooltip.vue'
import FilePickerPopup from './FilePickerPopup.vue'
import SlashCommandPickerPopup from './SlashCommandPickerPopup.vue'

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
const settingsButtonId = useId()
const textareaAnchorId = useId()

// File picker popup state (@ mention)
const filePickerRef = ref(null)
const atCursorPosition = ref(null)  // cursor position right after the '@' character
const fileMirroredLength = ref(0)   // length of filter text mirrored into textarea after '@'

// Slash command picker popup state (/ at start)
const slashPickerRef = ref(null)
const slashCursorPosition = ref(null)  // cursor position right after the '/' character
const slashMirroredLength = ref(0)     // length of filter text mirrored into textarea after '/'

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
}))

// Model options for the dropdown
const modelOptions = Object.values(MODEL).map(value => ({
    value,
    label: MODEL_LABELS[value],
}))

// Effort options for the dropdown
const effortOptions = Object.values(EFFORT).map(value => ({
    value,
    label: EFFORT_LABELS[value],
}))

// Thinking options for the dropdown (use string values for wa-select compatibility)
const thinkingOptions = [
    { value: 'true', label: THINKING_LABELS[true] },
    { value: 'false', label: THINKING_LABELS[false] },
]

// Claude in Chrome options for the dropdown (use string values for wa-select compatibility)
const claudeInChromeOptions = [
    { value: 'true', label: CLAUDE_IN_CHROME_LABELS[true] },
    { value: 'false', label: CLAUDE_IN_CHROME_LABELS[false] },
]

// Summary text for the settings button (labels joined with middle dot)
const settingsSummary = computed(() => [
    MODEL_LABELS[selectedModel.value],
    EFFORT_DISPLAY_LABELS[selectedEffort.value],
    THINKING_DISPLAY_LABELS[selectedThinking.value],
    CLAUDE_IN_CHROME_DISPLAY_LABELS[selectedClaudeInChrome.value],
    PERMISSION_MODE_LABELS[selectedPermissionMode.value],
].join(' · '))

// Selected permission mode for the current session
const selectedPermissionMode = ref('default')

// Selected model for the current session
const selectedModel = ref('opus')

// Selected effort level for the current session
const selectedEffort = ref('medium')

// Selected thinking mode for the current session
const selectedThinking = ref(true)

// Selected Claude in Chrome mode for the current session
const selectedClaudeInChrome = ref(true)

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
    let text = 'Type your message... Use / for commands, @ for file paths'
    if (!settingsStore.isTouchDevice) {
        const keys = settingsStore.isMac ? '⌘↵ or Ctrl↵' : 'Ctrl↵ or Meta↵'
        text += `, ${keys} to send`
    }
    return text
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
const activeClaudeInChrome = ref(null)

// Detect whether the user has changed the dropdowns from their reference values.
// This works regardless of process state (used for Reset button visibility).
const hasDropdownsChanged = computed(() =>
    selectedModel.value !== activeModel.value ||
    selectedPermissionMode.value !== activePermissionMode.value ||
    selectedEffort.value !== activeEffort.value ||
    selectedThinking.value !== activeThinking.value ||
    selectedClaudeInChrome.value !== activeClaudeInChrome.value
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

// Determine the effective Claude in Chrome mode for the current session.
function resolveClaudeInChrome(sess) {
    if (settingsStore.isAlwaysApplyDefaultClaudeInChrome) {
        return settingsStore.getDefaultClaudeInChrome
    }
    return sess?.claude_in_chrome ?? settingsStore.getDefaultClaudeInChrome
}

// Sync permission mode and model when session changes
watch(() => props.sessionId, (newId) => {
    const sess = store.getSession(newId)
    const resolvedPermission = resolvePermissionMode(sess)
    const resolvedModel = resolveModel(sess)
    const resolvedEffort = resolveEffort(sess)
    const resolvedThinking = resolveThinking(sess)
    const resolvedClaudeInChrome = resolveClaudeInChrome(sess)
    selectedPermissionMode.value = resolvedPermission
    selectedModel.value = resolvedModel
    selectedEffort.value = resolvedEffort
    selectedThinking.value = resolvedThinking
    selectedClaudeInChrome.value = resolvedClaudeInChrome
    // Initialize active values from the session, falling back to resolved defaults
    // so that dropdown change detection works even when no process is active.
    activePermissionMode.value = sess?.permission_mode || resolvedPermission
    activeModel.value = sess?.selected_model || resolvedModel
    activeEffort.value = sess?.effort || resolvedEffort
    activeThinking.value = sess?.thinking_enabled ?? resolvedThinking
    activeClaudeInChrome.value = sess?.claude_in_chrome ?? resolvedClaudeInChrome
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

// When the default Claude in Chrome setting changes, update the dropdown for sessions that should follow the default.
watch(
    () => [settingsStore.getDefaultClaudeInChrome, settingsStore.isAlwaysApplyDefaultClaudeInChrome],
    () => {
        if (processIsActive.value) return
        const sess = store.getSession(props.sessionId)
        const resolved = resolveClaudeInChrome(sess)
        selectedClaudeInChrome.value = resolved
        activeClaudeInChrome.value = resolved
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

// React when claude_in_chrome data arrives from backend.
watch(
    () => store.getSession(props.sessionId)?.claude_in_chrome,
    (newValue) => {
        if (newValue != null) {
            activeClaudeInChrome.value = newValue
            if (!processIsActive.value) {
                selectedClaudeInChrome.value = newValue
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
 * Detects '@' insertion to trigger the file picker popup.
 * Detects '/' at position 0 to trigger the slash command picker popup.
 * Also notifies the server that the user is actively drafting (debounced).
 */
function onInput(event) {
    const newText = event.target.value
    const oldText = messageText.value

    // Detect single character insertion
    if (newText.length === oldText.length + 1) {
        const inner = textareaRef.value?.shadowRoot?.querySelector('textarea')
        const cursorPos = inner?.selectionStart

        // Detect '@' to trigger file picker (only at start of text or after whitespace)
        if (!filePickerRef.value?.isOpen && cursorPos > 0 && newText[cursorPos - 1] === '@'
            && (cursorPos === 1 || /\s/.test(newText[cursorPos - 2]))) {
            atCursorPosition.value = cursorPos  // right after the '@'
            fileMirroredLength.value = 0
            nextTick(() => filePickerRef.value?.open())
        }

        // Detect '/' at position 0 (first character of the message) to trigger slash command picker
        if (!slashPickerRef.value?.isOpen && cursorPos === 1 && newText[0] === '/') {
            slashCursorPosition.value = cursorPos  // right after the '/'
            slashMirroredLength.value = 0
            nextTick(() => slashPickerRef.value?.open())
        }
    }

    messageText.value = newText
    adjustTextareaHeight()
    // Notify server that user is actively preparing a message (debounced)
    // This prevents auto-stop of the process due to inactivity timeout
    notifyUserDraftUpdated(props.sessionId)
}

/**
 * Update textarea content programmatically (without triggering input events).
 * Sets the value on the Vue reactive ref, the wa-textarea web component,
 * and the inner shadow DOM textarea.
 */
function updateTextareaContent(newText) {
    messageText.value = newText
    if (textareaRef.value) {
        textareaRef.value.value = newText
        const inner = textareaRef.value.shadowRoot?.querySelector('textarea')
        if (inner) {
            inner.value = newText
        }
    }
    adjustTextareaHeight()
}

/**
 * Mirror popup filter text into the textarea at the given cursor position.
 * Replaces the previously mirrored text (tracked by mirroredLengthRef) with
 * the new filter text, keeping surrounding content intact.
 */
function mirrorFilterToTextarea(pos, mirroredLengthRef, filterText) {
    if (pos == null) return

    const currentText = messageText.value
    const before = currentText.slice(0, pos)
    const after = currentText.slice(pos + mirroredLengthRef.value)
    const newText = before + filterText + after

    mirroredLengthRef.value = filterText.length
    updateTextareaContent(newText)
}

/**
 * Handle filter text changes from the file picker popup.
 * Mirrors the typed filter text into the textarea right after the '@'.
 */
function onFilePickerFilterChange(filterText) {
    mirrorFilterToTextarea(atCursorPosition.value, fileMirroredLength, filterText)
}

/**
 * Handle filter text changes from the slash command picker popup.
 * Mirrors the typed filter text into the textarea right after the '/'.
 */
function onSlashPickerFilterChange(filterText) {
    mirrorFilterToTextarea(slashCursorPosition.value, slashMirroredLength, filterText)
}

/**
 * Handle file selection from the file picker popup.
 * Inserts the relative path right after the '@' character at the recorded position.
 */
async function onFilePickerSelect(relativePath) {
    const pos = atCursorPosition.value
    if (pos != null && pos <= messageText.value.length) {
        const before = messageText.value.slice(0, pos)
        // Skip the mirrored filter text that was transparently inserted
        const after = messageText.value.slice(pos + fileMirroredLength.value)
        // Add a trailing space unless the text after already starts with one
        const space = after.startsWith(' ') ? '' : ' '
        const newText = before + relativePath + space + after
        messageText.value = newText

        // Force update the web component and inner textarea
        if (textareaRef.value) {
            textareaRef.value.value = newText
            const inner = textareaRef.value.shadowRoot?.querySelector('textarea')
            if (inner) {
                inner.value = newText
                const newPos = pos + relativePath.length + space.length
                inner.setSelectionRange(newPos, newPos)
            }
        }
    }

    atCursorPosition.value = null
    fileMirroredLength.value = 0
    await nextTick()
    textareaRef.value?.focus()
    adjustTextareaHeight()
}

/**
 * Handle file picker popup close (without selection).
 * Returns focus to the textarea and positions the cursor after the
 * trigger character + any filter text that was mirrored.
 */
function onFilePickerClose() {
    const pos = atCursorPosition.value
    const mirrorLen = fileMirroredLength.value
    atCursorPosition.value = null
    fileMirroredLength.value = 0

    textareaRef.value?.focus()
    if (pos != null) {
        const inner = textareaRef.value?.shadowRoot?.querySelector('textarea')
        if (inner) {
            const cursorTarget = pos + mirrorLen
            inner.setSelectionRange(cursorTarget, cursorTarget)
        }
    }
}

/**
 * Handle slash command selection from the slash command picker popup.
 * Replaces the entire textarea content with the selected command text.
 */
async function onSlashCommandSelect(commandText) {
    slashCursorPosition.value = null
    slashMirroredLength.value = 0
    messageText.value = commandText

    // Force update the web component and inner textarea
    if (textareaRef.value) {
        textareaRef.value.value = commandText
        const inner = textareaRef.value.shadowRoot?.querySelector('textarea')
        if (inner) {
            inner.value = commandText
            const newPos = commandText.length
            inner.setSelectionRange(newPos, newPos)
        }
    }

    await nextTick()
    textareaRef.value?.focus()
    adjustTextareaHeight()
}

/**
 * Handle slash command picker popup close (without selection).
 * Returns focus to the textarea and positions the cursor after the
 * trigger character + any filter text that was mirrored.
 */
function onSlashCommandPickerClose() {
    const pos = slashCursorPosition.value
    const mirrorLen = slashMirroredLength.value
    slashCursorPosition.value = null
    slashMirroredLength.value = 0

    textareaRef.value?.focus()
    if (pos != null) {
        const inner = textareaRef.value?.shadowRoot?.querySelector('textarea')
        if (inner) {
            const cursorTarget = pos + mirrorLen
            inner.setSelectionRange(cursorTarget, cursorTarget)
        }
    }
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
        claude_in_chrome: selectedClaudeInChrome.value,
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
        activeClaudeInChrome.value = selectedClaudeInChrome.value

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
        if (activeClaudeInChrome.value !== null) {
            selectedClaudeInChrome.value = activeClaudeInChrome.value
        }
    }
}
</script>

<template>
    <div class="message-input">
        <wa-textarea
            ref="textareaRef"
            :id="textareaAnchorId"
            :value.prop="messageText"
            :placeholder="placeholderText"
            rows="3"
            resize="none"
            @input="onInput"
            @keydown="onKeydown"
            @paste="onPaste"
            @focus="adjustTextareaHeight"
        ></wa-textarea>

        <!-- File picker popup triggered by @ -->
        <FilePickerPopup
            ref="filePickerRef"
            :session-id="sessionId"
            :project-id="projectId"
            :anchor-id="textareaAnchorId"
            @select="onFilePickerSelect"
            @close="onFilePickerClose"
            @filter-change="onFilePickerFilterChange"
        />

        <!-- Slash command picker popup triggered by / at start -->
        <SlashCommandPickerPopup
            ref="slashPickerRef"
            :project-id="projectId"
            :anchor-id="textareaAnchorId"
            @select="onSlashCommandSelect"
            @close="onSlashCommandPickerClose"
            @filter-change="onSlashPickerFilterChange"
        />

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
                <!-- Settings summary button + popover -->
                <wa-button
                    :id="settingsButtonId"
                    appearance="plain"
                    variant="neutral"
                    size="small"
                    class="settings-button"
                >
                    <wa-icon name="gear"></wa-icon><span>{{ settingsSummary }}</span>
                </wa-button>
                <wa-popover
                    :for="settingsButtonId"
                    placement="top"
                    class="settings-popover"
                >
                    <div class="settings-panel">
                        <div class="setting-row">
                            <label class="setting-label">Model</label>
                            <wa-select
                                :value.prop="selectedModel"
                                @change="selectedModel = $event.target.value"
                                size="small"
                                :disabled="isModelDisabled"
                            >
                                <wa-option v-for="option in modelOptions" :key="option.value" :value="option.value">
                                    {{ option.label }}
                                </wa-option>
                            </wa-select>
                            <span class="setting-help">Can only be changed on your turn.</span>
                        </div>
                        <div class="setting-row">
                            <label class="setting-label">Effort</label>
                            <wa-select
                                :value.prop="selectedEffort"
                                @change="selectedEffort = $event.target.value"
                                size="small"
                                :disabled="isEffortThinkingDisabled"
                            >
                                <wa-option v-for="option in effortOptions" :key="option.value" :value="option.value">
                                    {{ option.label }}
                                </wa-option>
                            </wa-select>
                            <span class="setting-help">Cannot be changed while a process is running.</span>
                        </div>
                        <div class="setting-row">
                            <label class="setting-label">Thinking</label>
                            <wa-select
                                :value.prop="String(selectedThinking)"
                                @change="selectedThinking = $event.target.value === 'true'"
                                size="small"
                                :disabled="isEffortThinkingDisabled"
                            >
                                <wa-option v-for="option in thinkingOptions" :key="option.value" :value="option.value" :label="option.label">
                                    {{ option.label }}
                                </wa-option>
                            </wa-select>
                            <span class="setting-help">Cannot be changed while a process is running.</span>
                        </div>
                        <div class="setting-row">
                            <label class="setting-label">Permission</label>
                            <wa-select
                                :value.prop="selectedPermissionMode"
                                @change="selectedPermissionMode = $event.target.value"
                                size="small"
                                :disabled="isDisabled"
                            >
                                <wa-option v-for="option in permissionModeOptions" :key="option.value" :value="option.value" :label="option.label">
                                    <span>{{ option.label }}</span>
                                    <span class="option-description">{{ option.description }}</span>
                                </wa-option>
                            </wa-select>
                            <span class="setting-help">Can be changed at any time, even while Claude is working.</span>
                        </div>
                        <div class="setting-row">
                            <label class="setting-label">Claude built-in Chrome MCP</label>
                            <wa-select
                                :value.prop="String(selectedClaudeInChrome)"
                                @change="selectedClaudeInChrome = $event.target.value === 'true'"
                                size="small"
                                :disabled="isEffortThinkingDisabled"
                            >
                                <wa-option v-for="option in claudeInChromeOptions" :key="option.value" :value="option.value" :label="option.label">
                                    {{ option.label }}
                                </wa-option>
                            </wa-select>
                            <span class="setting-help">Cannot be changed while a process is running.</span>
                        </div>
                    </div>
                </wa-popover>

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

.settings-button {
    wa-icon {
        display: none;
    }
    min-width: 0;
    flex-shrink: 1;
    &::part(label) {
        white-space: wrap;
        font-weight: normal;
        font-size: var(--wa-font-size-s);
    }
}

.settings-popover {
    --max-width: 95vw;
    --arrow-size: 12px;
}

.settings-panel {
    display: flex;
    flex-direction: column;
    gap: var(--wa-space-m);
}

.setting-row {
    display: flex;
    flex-direction: column;
    gap: var(--wa-space-2xs);
}

.setting-label {
    font-size: var(--wa-font-size-s);
    font-weight: var(--wa-font-weight-semibold);
}

.setting-help {
    font-size: var(--wa-font-size-xs);
    color: var(--wa-color-text-quiet);
}

.option-description {
    display: block;
    font-size: var(--wa-font-size-s);
    color: var(--wa-color-text-quiet);
}

.message-input-actions {
    display: flex;
    gap: var(--wa-space-s);
    flex-shrink: 1;
    min-width: 0;
    align-items: center;
    justify-content: flex-end;
    max-width: calc(100% - 6rem);

    .cancel-button, .reset-button, .send-button {
        flex-shrink: 0;
        wa-icon {
            display: none;
        }
        & > span {
            display: inline-block;
        }
    }
}

/* On narrow widths, show only icons for action buttons */
@container message-input (width < 35rem) {
    .message-input-actions {
        .settings-button {
            &::part(label) {
                line-height: 1.1;
            }
            &::part(base) {
                padding-inline: var(--wa-space-2xs);
            }
        }

        gap: var(--wa-space-2xs);

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
@container message-input (width < 24rem) {
    .message-input-actions {
        .settings-button {
            wa-icon {
                display: block;
            }
            & > span {
                display: none;
            }
            &::part(base) {
                padding-inline: var(--wa-space-s);
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
