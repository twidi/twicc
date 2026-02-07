<script setup>
import { ref, watch, nextTick, computed } from 'vue'
import { useDataStore } from '../stores/data'
import { useSettingsStore } from '../stores/settings'
import { requestTitleSuggestion } from '../composables/useWebSocket'

const props = defineProps({
    session: {
        type: Object,
        default: null,
    },
})

const emit = defineEmits(['saved'])

const store = useDataStore()
const settingsStore = useSettingsStore()

const formId = computed(() => `session-rename-form-${props.session?.id || 'none'}`)
const dialogRef = ref(null)
const titleInputRef = ref(null)

const localTitle = ref('')
const isSaving = ref(false)
const errorMessage = ref('')
const showContextHint = ref(false)  // Show hint when opened during message send
const isLoadingSuggestion = ref(false)

// Title generation settings
const titleGenerationEnabled = computed(() => settingsStore.isTitleGenerationEnabled)
const titleSystemPrompt = computed(() => settingsStore.getTitleSystemPrompt)

// Computed for the suggestion from store
const suggestion = computed(() => {
    if (!props.session) return null
    return store.getTitleSuggestion(props.session.id)
})

// Sync form values when session changes
watch(
    () => props.session,
    (newSession) => {
        if (newSession) {
            localTitle.value = newSession.title || ''
        }
    },
    { immediate: true }
)

// Watch for suggestion arrival
watch(
    () => props.session && store.getTitleSuggestion(props.session.id),
    (newSuggestion) => {
        if (newSuggestion && isLoadingSuggestion.value) {
            isLoadingSuggestion.value = false
        }
    }
)

/**
 * Sync Web Component values with local state when dialog opens.
 */
function syncFormState() {
    nextTick(() => {
        if (titleInputRef.value) {
            titleInputRef.value.value = localTitle.value
        }
    })
}

/**
 * Focus the title input after the dialog opening animation completes.
 * Positions cursor at the end of the text.
 */
function focusTitleInput() {
    const input = titleInputRef.value
    if (!input) return
    input.focus()
    // Move cursor to end of text
    const len = input.value?.length || 0
    input.setSelectionRange(len, len)
}

/**
 * Open the dialog.
 * @param {Object} options
 * @param {boolean} options.showHint - Show contextual hint (when opened during message send)
 * @param {Object} options.session - The session to rename (used immediately since props
 *     may not be updated yet when called synchronously after setting the parent ref)
 */
function open({ showHint = false, session = null } = {}) {
    errorMessage.value = ''
    showContextHint.value = showHint

    // Use the session passed directly, falling back to props.session
    // (props.session may not be updated yet in the same tick)
    const currentSession = session || props.session

    if (currentSession) {
        localTitle.value = currentSession.title || ''
    }
    syncFormState()

    if (dialogRef.value) {
        dialogRef.value.open = true
    }

    if (!currentSession) return

    // Skip if title generation is disabled
    if (!titleGenerationEnabled.value) return

    const sessionId = currentSession.id
    const existingSuggestion = store.getTitleSuggestion(sessionId)
    const systemPrompt = titleSystemPrompt.value

    if (currentSession.draft) {
        // DRAFT: use message from store, redo if message changed
        const currentPrompt = store.getDraftMessage(sessionId)?.message?.trim()
        const previousPrompt = store.getTitleSuggestionSourcePrompt(sessionId)

        if (!currentPrompt) return  // No message, no suggestion

        if (!existingSuggestion || previousPrompt !== currentPrompt) {
            isLoadingSuggestion.value = true
            requestTitleSuggestion(sessionId, currentPrompt, systemPrompt)
        }
    } else {
        // EXISTING or NEW SESSION: use first message from DB
        if (!existingSuggestion) {
            isLoadingSuggestion.value = true
            requestTitleSuggestion(sessionId, null, systemPrompt)
        }
    }
}

/**
 * Close the dialog.
 */
function close() {
    if (dialogRef.value) {
        dialogRef.value.open = false
    }
}

/**
 * Handle title input change.
 */
function onTitleInput(event) {
    localTitle.value = event.target.value
}

/**
 * Apply the suggested title to the input field.
 * Keep the suggestion in store so user can still regenerate or see it.
 */
function applySuggestion() {
    if (suggestion.value) {
        localTitle.value = suggestion.value
        if (titleInputRef.value) {
            titleInputRef.value.value = suggestion.value
        }
    }
}

/**
 * Request a new title suggestion using the stored prompt.
 */
function regenerateSuggestion() {
    if (!props.session) return

    const sessionId = props.session.id
    const storedPrompt = store.getTitleSuggestionSourcePrompt(sessionId)

    if (storedPrompt) {
        isLoadingSuggestion.value = true
        requestTitleSuggestion(sessionId, storedPrompt, titleSystemPrompt.value)
    }
}

/**
 * Save the session title.
 * For draft sessions: store locally in the session object.
 * For real sessions: call the API to rename.
 */
async function handleSave() {
    if (!props.session) return

    const trimmedTitle = localTitle.value.trim()

    if (!trimmedTitle) {
        errorMessage.value = 'Title cannot be empty'
        return
    }

    if (trimmedTitle.length > 200) {
        errorMessage.value = 'Title must be 200 characters or less'
        return
    }

    // For draft sessions, just update locally (no API call)
    if (props.session.draft) {
        store.updateSession({ ...props.session, title: trimmedTitle })
        // Also persist to IndexedDB for page refresh recovery
        store.setDraftTitle(props.session.id, trimmedTitle)
        emit('saved')
        close()
        return
    }

    // For real sessions, call the API
    isSaving.value = true
    errorMessage.value = ''

    try {
        await store.renameSession(
            props.session.project_id,
            props.session.id,
            trimmedTitle
        )
        emit('saved')
        close()
    } catch (error) {
        errorMessage.value = error.message || 'Failed to rename session'
    } finally {
        isSaving.value = false
    }
}

// Expose methods for parent components
defineExpose({
    open,
    close,
})
</script>

<template>
    <wa-dialog
        ref="dialogRef"
        label="Rename Session"
        class="session-rename-dialog"
        @wa-show="syncFormState"
        @wa-after-show="focusTitleInput"
    >
        <form v-if="session" :id="formId" class="dialog-content" @submit.prevent="handleSave">
            <!-- Contextual hint when opened during message send -->
            <p v-if="showContextHint" class="context-hint">
                While Claude is working, you may want to give this session a more descriptive name.
            </p>

            <!-- Title suggestion (only if enabled in settings) -->
            <div v-if="titleGenerationEnabled && (isLoadingSuggestion || suggestion)" class="suggestion-section">
                <div class="suggestion-header">
                    <span class="suggestion-label">Suggestion:</span>
                    <wa-button
                        v-if="suggestion"
                        variant="neutral"
                        appearance="plain"
                        size="small"
                        class="regenerate-button"
                        @click="regenerateSuggestion"
                    >
                        <wa-icon name="rotate" label="Regenerate"></wa-icon>
                    </wa-button>
                </div>
                <div v-if="isLoadingSuggestion" class="suggestion-loading">
                    <wa-spinner size="small"></wa-spinner>
                    <span>Generating...</span>
                </div>
                <a v-else href="#" class="suggestion-link" @click.prevent="applySuggestion">
                    {{ suggestion }}
                </a>
            </div>

            <div class="form-group">
                <label class="form-label">Title</label>
                <wa-input
                    ref="titleInputRef"
                    :value.prop="localTitle"
                    @input="onTitleInput"
                    placeholder="Session title"
                    maxlength="200"
                ></wa-input>
                <div class="form-hint">Max 200 characters</div>
            </div>

            <!-- Error message -->
            <wa-callout v-if="errorMessage" variant="danger" size="small">
                {{ errorMessage }}
            </wa-callout>
        </form>

        <!-- Footer buttons -->
        <div slot="footer" class="dialog-footer">
            <wa-button variant="neutral" appearance="outlined" @click="close" :disabled="isSaving">
                Cancel
            </wa-button>
            <wa-button type="submit" :form="formId" variant="brand" :disabled="isSaving">
                <wa-spinner v-if="isSaving" slot="prefix"></wa-spinner>
                Save
            </wa-button>
        </div>
    </wa-dialog>
</template>

<style scoped>
.session-rename-dialog {
    --width: min(500px, calc(100vw - 2rem));
}

.dialog-content {
    display: flex;
    flex-direction: column;
    gap: var(--wa-space-m);
}

.form-group {
    display: flex;
    flex-direction: column;
    gap: var(--wa-space-xs);
}

.form-label {
    font-size: var(--wa-font-size-s);
    font-weight: var(--wa-font-weight-semibold);
}

.form-hint {
    font-size: var(--wa-font-size-xs);
    color: var(--wa-color-text-quiet);
}

.context-hint {
    margin: 0;
    font-size: var(--wa-font-size-s);
    color: var(--wa-color-text-quiet);
}

.suggestion-section {
    margin-bottom: var(--wa-space-m);
}

.suggestion-section {
    display: flex;
    flex-direction: column;
    gap: var(--wa-space-s);
    font-size: var(--wa-font-size-s);
}

.suggestion-header {
    display: flex;
    align-items: center;
    gap: var(--wa-space-xs);
}

.suggestion-label {
    color: var(--wa-color-text-quiet);
}

.suggestion-loading {
    display: flex;
    align-items: center;
    gap: var(--wa-space-xs);
    color: var(--wa-color-text-quiet);
}

.suggestion-link {
    color: var(--wa-color-brand);
    text-decoration: none;
    cursor: pointer;
}

.suggestion-link:hover {
    text-decoration: underline;
}

.regenerate-button {
    opacity: 0.6;
    transition: opacity 0.15s;
    flex-shrink: 0;
    font-size: var(--wa-font-size-3xs);
    &::part(label) {
        scale: 1.5;
    }
    margin-block: calc(-3 * var(--wa-space-2xs));
}

.regenerate-button:hover {
    opacity: 1;
}

.dialog-footer {
    display: flex;
    gap: var(--wa-space-s);
    justify-content: flex-end;
}
</style>
