<script setup>
import { ref, watch, computed } from 'vue'
import { useDataStore } from '../../../stores/data'
import { useSettingsStore } from '../../../stores/settings'
import { requestTitleSuggestion } from '../../../composables/useWebSocket'
import { getProviderLabel } from '../../../providers'
import { buildSessionTitlePatch, showAutomaticTitleHint } from '../../../utils/sessionTitle.js'
import {
    TITLE_SUGGESTION_MODEL_LABELS,
    resolveSessionTitleModel,
    titleModelAlternatives,
    titleModelForProvider,
} from '../../../constants'

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
const automaticTitleHint = computed(() => showAutomaticTitleHint(props.session))
const isLoadingSuggestion = ref(false)
// Set when the request never left the browser (WS down, unknown provider): no
// reply will ever come, so the spinner must stop on the spot.
const suggestionSendFailed = ref(false)

// Title generation settings
const titleGenerationEnabled = computed(() => settingsStore.isTitleGenerationEnabled)
const titleSystemPrompt = computed(() => settingsStore.getTitleSystemPrompt)

// Computed for the suggestion from store
const suggestion = computed(() => {
    if (!props.session) return null
    return store.getTitleSuggestion(props.session.id)
})

const suggestionEntry = computed(() => {
    if (!props.session) return null
    return store.getTitleSuggestionEntry(props.session.id)
})

// The title model asked for explicitly with a "Try with …" link; null means
// the default one for this session (the settings, then the session's provider).
// A one-off choice for this dialog: the setting is never touched.
const chosenTitleModel = ref(null)

const defaultTitleModel = computed(() =>
    resolveSessionTitleModel(settingsStore.getEffectiveTitleSuggestionModel, props.session?.provider),
)
const requestedTitleModel = computed(() => chosenTitleModel.value || defaultTitleModel.value)

// What the user is looking at: the model that produced the suggestion (which
// is not the requested one after a backend fallback), else the requested one.
// While a request runs, the stored suggestion is the previous one's: only the
// requested model is true.
const displayedTitleModel = computed(() => {
    if (isLoadingSuggestion.value) return requestedTitleModel.value
    return (suggestion.value && titleModelForProvider(suggestionEntry.value?.titleProvider))
        || requestedTitleModel.value
})
const displayedTitleModelLabel = computed(() => TITLE_SUGGESTION_MODEL_LABELS[displayedTitleModel.value] || null)

// The other enabled title models, to try instead of the displayed one.
const alternativeTitleModels = computed(() =>
    titleModelAlternatives(displayedTitleModel.value, settingsStore.enabledProviders),
)

// Why there is no suggestion, when the user can act on it. ``no_prompt`` is
// excluded on purpose: nothing to summarize is not a failure, and the section
// stays hidden exactly as it did before.
const suggestionErrorMessage = computed(() => {
    if (suggestionSendFailed.value) return 'Could not reach the server.'
    const error = suggestionEntry.value?.error
    if (error === 'no_provider_available') return 'No provider is available to generate a title.'
    if (error === 'generation_failed') return 'Could not generate a title suggestion.'
    return null
})

/**
 * The message a draft would be summarized from: its ephemeral prompt, else the
 * message kept in the store. Null for a real session, whose source message the
 * backend reads from the DB.
 * @param {Object} session
 * @returns {string|null}
 */
function draftPromptFor(session) {
    if (!session?.draft && !session?.ephemeral) return null
    return (session.ephemeralPrompt?.text || store.getDraftMessage(session.id)?.message)?.trim() || null
}

/**
 * Fire a suggestion request and drive the spinner from its outcome. A request
 * that never left the browser gets no reply, so it stops the spinner itself
 * and shows the retry affordance.
 * @param {string} sessionId
 * @param {string|null} prompt
 */
function startSuggestion(sessionId, prompt) {
    suggestionSendFailed.value = false
    isLoadingSuggestion.value = true
    // An explicit choice is strict: when the user turns a model down for
    // another, the backend must not answer with the one they turned down.
    const options = chosenTitleModel.value ? { model: chosenTitleModel.value, noFallback: true } : {}
    if (!requestTitleSuggestion(sessionId, prompt, titleSystemPrompt.value, options)) {
        isLoadingSuggestion.value = false
        suggestionSendFailed.value = true
    }
}

// The backend generated through another provider than the one requested: a
// runtime failure, or a session whose own provider is disabled under "match
// session provider". A forced choice whose provider is disabled never lands
// here — it is already resolved before the request leaves — and neither does an
// explicit "Try with …" (strict).
const isFallbackSuggestion = computed(() => {
    const entry = suggestionEntry.value
    if (!entry?.suggestion || !entry.titleProvider || !entry.requestedProvider) return false
    return entry.titleProvider !== entry.requestedProvider
})

const providerLabel = computed(() => getProviderLabel(props.session?.provider))

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

// Watch for suggestion response arrival (success or failure).
// We watch the full entry (not just the suggestion value) so the spinner
// stops even when all backend retries failed and suggestion is null.
watch(
    () => props.session ? store.getTitleSuggestionEntry(props.session.id) : null,
    (entry) => {
        if (entry && isLoadingSuggestion.value) {
            isLoadingSuggestion.value = false
        }
    }
)

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
    suggestionSendFailed.value = false
    chosenTitleModel.value = null
    showContextHint.value = showHint

    // Use the session passed directly, falling back to props.session
    // (props.session may not be updated yet in the same tick)
    const currentSession = session || props.session

    if (currentSession) {
        localTitle.value = currentSession.title || ''
    }
    if (dialogRef.value) {
        dialogRef.value.open = true
    }

    if (!currentSession) return

    // Skip if title generation is disabled
    if (!titleGenerationEnabled.value) return

    const sessionId = currentSession.id
    if (currentSession.draft || currentSession.ephemeral) {
        // DRAFT: use message from store, redo if message changed
        const currentPrompt = draftPromptFor(currentSession)
        const previousPrompt = store.getTitleSuggestionSourcePrompt(sessionId)

        if (!currentPrompt) return  // No message, no suggestion

        if (!store.getTitleSuggestion(sessionId) || previousPrompt !== currentPrompt) {
            startSuggestion(sessionId, currentPrompt)
        }
    } else {
        // EXISTING or NEW SESSION: the backend reads the user messages from the
        // DB. Asked again at every opening, never served from the store: the
        // conversation moves on, and a suggestion kept from an earlier opening
        // (or from the auto-applied first message) would describe an older state.
        startSuggestion(sessionId, null)
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
    }
}

/**
 * Request a new title suggestion.
 *
 * Also the "Try again" action of the error state. A draft sends its own
 * message again (the stored prompt, or the draft's current one when the send
 * never reached the server). A real session sends no prompt, so the backend
 * rebuilds the source from the conversation as it is now.
 */
function regenerateSuggestion() {
    if (!props.session) return

    const sessionId = props.session.id
    const isDraft = props.session.draft || props.session.ephemeral
    const prompt = isDraft ? (store.getTitleSuggestionSourcePrompt(sessionId) || draftPromptFor(props.session)) : null

    if (!isDraft) {
        startSuggestion(sessionId, null)
    } else if (prompt) {
        startSuggestion(sessionId, prompt)
    } else {
        // A draft left with no message: there is nothing to summarize, so drop
        // the error rather than leaving an inert button on screen.
        suggestionSendFailed.value = false
    }
}

/**
 * Generate the suggestion with another title model than the displayed one.
 * Only for this dialog: the setting is untouched. Regenerating afterwards keeps
 * the choice.
 * @param {string} model - A ``TITLE_SUGGESTION_MODEL`` value.
 */
function trySuggestionWith(model) {
    if (isLoadingSuggestion.value) return
    chosenTitleModel.value = model
    regenerateSuggestion()
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
    if (props.session.draft || props.session.ephemeral) {
        store.updateSession({ ...props.session, title: trimmedTitle })
        // Also persist to IndexedDB for page refresh recovery
        store.setDraftTitle(props.session.id, trimmedTitle)
        emit('saved')
        close()
        return
    }

    // Saving confirms a real title, even when its text is unchanged.
    const { title } = buildSessionTitlePatch(trimmedTitle)
    isSaving.value = true
    errorMessage.value = ''

    try {
        await store.renameSession(
            props.session.project_id,
            props.session.id,
            title
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
        @wa-after-show="focusTitleInput"
    >
        <form v-if="session" :id="formId" class="dialog-content" @submit.prevent="handleSave">
            <!-- Contextual hint when opened during message send -->
            <p v-if="showContextHint" class="context-hint">
                While {{ providerLabel }} is working, you may want to give this session a more descriptive name.
            </p>
            <p v-if="automaticTitleHint" class="context-hint">
                This title is automatic. Save to validate it and stop automatic updates.
            </p>

            <!-- Title suggestion (only if enabled in settings) -->
            <div
                v-if="titleGenerationEnabled && (isLoadingSuggestion || suggestion || suggestionErrorMessage)"
                class="suggestion-section"
            >
                <div class="suggestion-header">
                    <span class="suggestion-label">Suggestion:</span>
                    <span v-if="displayedTitleModelLabel" class="suggestion-model">
                        {{ displayedTitleModelLabel }}
                    </span>
                    <span v-if="isFallbackSuggestion" class="suggestion-fallback">(fallback)</span>
                    <wa-button
                        v-if="suggestion"
                        variant="neutral"
                        appearance="plain"
                        size="small"
                        class="regenerate-button reduced-height"
                        @click="regenerateSuggestion"
                    >
                        <wa-icon name="rotate" label="Regenerate"></wa-icon>
                    </wa-button>
                    <!-- One-off choice of another title model: a quiet text link
                         pushed to the right of the header line, wrapping under it
                         (still right-aligned) when the dialog is narrow. -->
                    <span v-if="alternativeTitleModels.length" class="suggestion-tries">
                        <a
                            v-for="model in alternativeTitleModels"
                            :key="model"
                            href="#"
                            class="suggestion-try"
                            :class="{ 'is-disabled': isLoadingSuggestion }"
                            :aria-disabled="isLoadingSuggestion ? 'true' : null"
                            @click.prevent="trySuggestionWith(model)"
                        >Try with {{ TITLE_SUGGESTION_MODEL_LABELS[model] }}</a>
                    </span>
                </div>
                <div v-if="isLoadingSuggestion" class="suggestion-loading">
                    <wa-spinner size="small"></wa-spinner>
                    <span>Generating{{ displayedTitleModelLabel ? ` with ${displayedTitleModelLabel}` : '' }}...</span>
                </div>
                <div v-else-if="suggestionErrorMessage" class="suggestion-error">
                    <span>{{ suggestionErrorMessage }}</span>
                    <wa-button
                        variant="neutral"
                        appearance="plain"
                        size="small"
                        class="reduced-height"
                        @click="regenerateSuggestion"
                    >
                        <wa-icon slot="start" name="rotate"></wa-icon>
                        Try again
                    </wa-button>
                </div>
                <template v-else>
                    <a href="#" class="suggestion-link" @click.prevent="applySuggestion">
                        {{ suggestion }}
                    </a>
                    <div class="form-hint">Click the suggestion above to use it</div>
                </template>
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
                <wa-spinner v-if="isSaving" slot="start"></wa-spinner>
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
    flex-wrap: wrap;
    align-items: center;
    column-gap: var(--wa-space-xs);
    row-gap: var(--wa-space-2xs);
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

.suggestion-model {
    font-weight: var(--wa-font-weight-semibold);
}

.suggestion-fallback {
    color: var(--wa-color-text-quiet);
    font-size: var(--wa-font-size-xs);
}

.suggestion-tries {
    /* Right edge of the header line; when it wraps, it stays right-aligned. */
    display: flex;
    flex-wrap: wrap;
    justify-content: flex-end;
    gap: var(--wa-space-xs);
    margin-inline-start: auto;
}

.suggestion-try {
    font-size: var(--wa-font-size-xs);
    color: var(--wa-color-text-quiet);
    text-decoration: none;
    white-space: nowrap;
    cursor: pointer;
}

.suggestion-try:hover {
    color: var(--wa-color-brand);
    text-decoration: underline;
}

.suggestion-try.is-disabled {
    opacity: 0.5;
    pointer-events: none;
}

.suggestion-error {
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
