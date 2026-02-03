<script setup>
import { ref, watch, nextTick } from 'vue'
import { useDataStore } from '../stores/data'

const props = defineProps({
    session: {
        type: Object,
        default: null,
    },
})

const emit = defineEmits(['saved'])

const store = useDataStore()

const dialogRef = ref(null)
const titleInputRef = ref(null)

const localTitle = ref('')
const isSaving = ref(false)
const errorMessage = ref('')
const showContextHint = ref(false)  // Show hint when opened during message send

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
 */
function open({ showHint = false } = {}) {
    errorMessage.value = ''
    showContextHint.value = showHint
    syncFormState()
    if (dialogRef.value) {
        dialogRef.value.open = true
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
        <form v-if="session" id="session-rename-form" class="dialog-content" @submit.prevent="handleSave">
            <!-- Contextual hint when opened during message send -->
            <p v-if="showContextHint" class="context-hint">
                While Claude is working, you may want to give this session a more descriptive name.
            </p>

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
            <wa-button type="submit" form="session-rename-form" variant="brand" :disabled="isSaving">
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

.dialog-footer {
    display: flex;
    gap: var(--wa-space-s);
    justify-content: flex-end;
}
</style>
