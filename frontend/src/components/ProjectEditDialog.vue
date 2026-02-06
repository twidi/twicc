<script setup>
// ProjectEditDialog.vue - Dialog for editing project name and color
import { ref, watch, nextTick } from 'vue'
import { useDataStore } from '../stores/data'
import { apiFetch } from '../utils/api'

const props = defineProps({
    project: {
        type: Object,
        default: null,
    },
})

const emit = defineEmits(['saved'])

const store = useDataStore()

// Refs for the dialog and form elements
const dialogRef = ref(null)
const nameInputRef = ref(null)
const colorPickerRef = ref(null)
const saveButtonRef = ref(null)

// Local state for form values
const localName = ref('')
const localColor = ref('')
const isSaving = ref(false)
const errorMessage = ref('')

// Sync form values when project changes
watch(
    () => props.project,
    (newProject) => {
        if (newProject) {
            // Pre-fill with displayName (computed from name, directory, or id)
            localName.value = store.getProjectDisplayName(newProject.id)
            localColor.value = newProject.color || ''
        }
    },
    { immediate: true }
)

/**
 * Sync Web Component values with local state when dialog opens.
 */
function syncFormState() {
    nextTick(() => {
        if (nameInputRef.value) {
            nameInputRef.value.value = localName.value
        }
        if (colorPickerRef.value) {
            colorPickerRef.value.value = localColor.value
        }
        // Set form attribute on save button (wa-button doesn't expose this as a property)
        if (saveButtonRef.value) {
            saveButtonRef.value.setAttribute('form', 'project-edit-form')
        }
    })
}

/**
 * Focus the name input after the dialog opening animation completes.
 * Positions cursor at the end of the text.
 */
function focusNameInput() {
    const input = nameInputRef.value
    if (!input) return
    input.focus()
    // Move cursor to end of text
    const len = input.value?.length || 0
    input.setSelectionRange(len, len)
}

/**
 * Open the dialog.
 */
function open() {
    errorMessage.value = ''
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
 * Handle name input change.
 */
function onNameInput(event) {
    localName.value = event.target.value
}

/**
 * Handle color picker change.
 */
function onColorChange(event) {
    localColor.value = event.target.value
}

/**
 * Save the project changes.
 */
async function handleSave() {
    if (!props.project) return

    // Trim whitespace from name
    const trimmedName = localName.value.trim()

    // Validate name length
    if (trimmedName.length > 25) {
        errorMessage.value = 'Name must be 25 characters or less'
        return
    }

    // Check uniqueness among other projects
    const otherProjects = store.getProjects.filter(p => p.id !== props.project.id)
    const isDuplicate = otherProjects.some(p => p.name && p.name === trimmedName)
    if (trimmedName && isDuplicate) {
        errorMessage.value = 'A project with this name already exists'
        return
    }

    isSaving.value = true
    errorMessage.value = ''

    let response
    try {
        response = await apiFetch(`/api/projects/${props.project.id}/`, {
            method: 'PUT',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                name: trimmedName || null,
                color: localColor.value || null,
            }),
        })
    } catch (error) {
        errorMessage.value = 'Network error. Please try again.'
        isSaving.value = false
        return
    }

    if (!response.ok) {
        const data = await response.json()
        errorMessage.value = data.error || 'Failed to save project'
        isSaving.value = false
        return
    }

    const updatedProject = await response.json()
    store.updateProject(updatedProject)
    emit('saved', updatedProject)
    isSaving.value = false
    close()
}

// Expose methods for parent components
defineExpose({
    open,
    close,
})
</script>

<template>
    <wa-dialog ref="dialogRef" label="Edit Project" class="project-edit-dialog" @wa-show="syncFormState" @wa-after-show="focusNameInput">
        <form v-if="project" id="project-edit-form" class="dialog-content" @submit.prevent="handleSave">
            <!-- Read-only info -->
            <div class="info-group">
                <label class="info-label">ID</label>
                <div class="info-value">{{ project.id }}</div>
            </div>
            <div v-if="project.directory" class="info-group">
                <label class="info-label">Directory</label>
                <div class="info-value">{{ project.directory }}</div>
            </div>

            <wa-divider></wa-divider>

            <!-- Editable fields -->
            <div class="form-group">
                <label class="form-label">Name</label>
                <wa-input
                    ref="nameInputRef"
                    :value.prop="localName"
                    @input="onNameInput"
                    placeholder="Project name"
                    maxlength="25"
                ></wa-input>
                <div class="form-hint">Optional display name (max 25 characters)</div>
            </div>

            <div class="form-group">
                <label class="form-label">Color</label>
                <wa-color-picker
                    ref="colorPickerRef"
                    :value.prop="localColor"
                    @change="onColorChange"
                ></wa-color-picker>
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
            <wa-button ref="saveButtonRef" type="submit" variant="brand" :disabled="isSaving">
                <wa-spinner v-if="isSaving" slot="prefix"></wa-spinner>
                Save
            </wa-button>
        </div>
    </wa-dialog>
</template>

<style scoped>
.project-edit-dialog {
    --width: min(600px, calc(100vw - 2rem));
}

.dialog-content {
    display: flex;
    flex-direction: column;
    gap: var(--wa-space-m);
}

.info-group {
    display: flex;
    flex-direction: column;
    gap: var(--wa-space-2xs);
}

.info-label {
    font-size: var(--wa-font-size-s);
    font-weight: var(--wa-font-weight-semibold);
    color: var(--wa-color-text-quiet);
}

.info-value {
    font-size: var(--wa-font-size-m);
    word-break: break-all;
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

.dialog-footer {
    display: flex;
    gap: var(--wa-space-s);
    justify-content: flex-end;
}
</style>
