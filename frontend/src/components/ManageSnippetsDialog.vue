<script setup>
// ManageSnippetsDialog.vue - Dialog for managing text snippets with scope grouping
import { ref, computed, nextTick, useId } from 'vue'
import { useTerminalConfigStore } from '../stores/terminalConfig'
import { useDataStore } from '../stores/data'
import ProjectBadge from './ProjectBadge.vue'
import { buildProjectTree, flattenProjectTree } from '../utils/projectTree'

const props = defineProps({
    currentProjectId: {
        type: String,
        default: null,
    },
})

const terminalConfigStore = useTerminalConfigStore()
const dataStore = useDataStore()

// ── Dialog refs ──────────────────────────────────────────────────────
const dialogRef = ref(null)
const saveButtonRef = ref(null)
const labelInputRef = ref(null)

const instanceId = useId()
const formId = `manage-snippets-form-${instanceId}`

// ── View state ───────────────────────────────────────────────────────
const view = ref('list') // 'list' or 'form'
const editScope = ref(null)    // scope being edited (null for new)
const editIndex = ref(null)    // index within scope (null for new)
const isDuplicate = ref(false)
const formData = ref(null)     // { label: '', text: '', appendEnter: true, scope: 'global' }
const errorMessage = ref('')
const warningMessage = ref('')

// ── Computed ─────────────────────────────────────────────────────────
const dialogLabel = computed(() => {
    if (view.value === 'list') return 'Manage Snippets'
    if (editIndex.value !== null) return 'Edit Snippet'
    return 'Add Snippet'
})

/** Active projects (non-stale, non-archived) — same filter as sidebar. */
const activeProjects = computed(() =>
    dataStore.getProjects.filter(p => !p.stale && !p.archived)
)

/** Named active projects (sorted by mtime desc — store order). */
const namedProjects = computed(() =>
    activeProjects.value.filter(p => p.name !== null)
)

/** Unnamed active projects as flattened directory tree (same as sidebar/new-session). */
const unnamedFlatTree = computed(() => {
    const unnamed = activeProjects.value.filter(p => p.name === null)
    const roots = buildProjectTree(unnamed)
    return flattenProjectTree(roots)
})

/** Color of the currently selected project scope (for the dot in the closed select). */
const selectedScopeProjectColor = computed(() => {
    if (!formData.value || formData.value.scope === 'global') return null
    const pid = formData.value.scope.slice('project:'.length)
    const project = dataStore.getProject(pid)
    return project?.color || null
})

// ── Form helpers ─────────────────────────────────────────────────────
function openAddForm() {
    editScope.value = null
    editIndex.value = null
    isDuplicate.value = false
    formData.value = {
        label: '',
        text: '',
        appendEnter: true,
        scope: props.currentProjectId ? `project:${props.currentProjectId}` : 'global',
    }
    errorMessage.value = ''
    warningMessage.value = ''
    view.value = 'form'
    nextTick(() => syncFormState())
}

function openEditForm(scope, index) {
    editScope.value = scope
    editIndex.value = index
    isDuplicate.value = false
    const snippet = terminalConfigStore.snippets[scope]?.[index]
    if (!snippet) return
    formData.value = {
        label: snippet.label,
        text: snippet.text,
        appendEnter: snippet.appendEnter,
        scope: scope,
    }
    errorMessage.value = ''
    warningMessage.value = ''
    view.value = 'form'
    nextTick(() => syncFormState())
}

function openDuplicateForm(scope, index) {
    editScope.value = null
    editIndex.value = null
    isDuplicate.value = true
    const snippet = terminalConfigStore.snippets[scope]?.[index]
    if (!snippet) return
    formData.value = {
        label: snippet.label,
        text: snippet.text,
        appendEnter: snippet.appendEnter,
        scope: scope,  // defaults to source scope
    }
    errorMessage.value = ''
    warningMessage.value = ''
    view.value = 'form'
    nextTick(() => syncFormState())
}

function cancelForm() {
    view.value = 'list'
    errorMessage.value = ''
    warningMessage.value = ''
}

// ── Helpers ──────────────────────────────────────────────────────────
/** Extract project ID from a scope string like "project:xxx" */
function projectIdFromScope(scope) {
    return scope.startsWith('project:') ? scope.slice('project:'.length) : null
}

// ── Validation & save ────────────────────────────────────────────────
function handleSave() {
    errorMessage.value = ''

    const trimmedLabel = formData.value.label.trim()
    const trimmedText = formData.value.text.trim()

    if (!trimmedLabel) {
        errorMessage.value = 'Label is required.'
        return
    }

    if (!trimmedText) {
        errorMessage.value = 'Text to send is required.'
        return
    }

    const selectedScope = formData.value.scope
    const snippetData = {
        label: trimmedLabel,
        text: trimmedText,
        appendEnter: formData.value.appendEnter,
    }

    // Check for duplicate label in same scope (warn but allow save on second submit)
    const scopeSnippets = terminalConfigStore.snippets[selectedScope] || []
    const hasDuplicateLabel = scopeSnippets.some((s, i) => {
        // Skip self when editing within the same scope
        if (editIndex.value !== null && editScope.value === selectedScope && i === editIndex.value) return false
        return s.label.trim().toLowerCase() === trimmedLabel.toLowerCase()
    })
    if (hasDuplicateLabel && !warningMessage.value) {
        warningMessage.value = 'A snippet with the same label already exists in this scope. Submit again to save anyway.'
        return
    }
    warningMessage.value = ''

    // Save
    if (editIndex.value !== null) {
        terminalConfigStore.updateSnippet(editScope.value, editIndex.value, snippetData, selectedScope)
    } else {
        terminalConfigStore.addSnippet(selectedScope, snippetData)
    }

    view.value = 'list'
    errorMessage.value = ''
    warningMessage.value = ''
}

// ── Dialog lifecycle ─────────────────────────────────────────────────
function syncFormState() {
    nextTick(() => {
        if (saveButtonRef.value) {
            saveButtonRef.value.setAttribute('form', formId)
        }
    })
}

function focusFirstInput() {
    if (view.value === 'form' && labelInputRef.value) {
        labelInputRef.value.focus()
    }
}

// Guard dialog events against bubbling from child wa-select/wa-dropdown
// (wa-select fires wa-show/wa-hide/wa-after-show when its dropdown opens/closes,
// and these bubble up to the wa-dialog which would close itself)
function handleDialogShow(e) {
    if (e.target !== dialogRef.value) return
    syncFormState()
}

function handleDialogAfterShow(e) {
    if (e.target !== dialogRef.value) return
    focusFirstInput()
}

function handleDialogHide(e) {
    if (e.target !== dialogRef.value) return
    // Let the dialog close normally
}

function open() {
    view.value = 'list'
    errorMessage.value = ''
    warningMessage.value = ''
    if (dialogRef.value) {
        dialogRef.value.open = true
    }
}

function close() {
    if (dialogRef.value) {
        dialogRef.value.open = false
    }
}

defineExpose({ open, close })
</script>

<template>
    <wa-dialog
        ref="dialogRef"
        :label="dialogLabel"
        class="manage-snippets-dialog"
        @wa-show="handleDialogShow"
        @wa-after-show="handleDialogAfterShow"
        @wa-hide="handleDialogHide"
    >
        <!-- ═══ LIST VIEW ═══ -->
        <div v-if="view === 'list'" class="dialog-content">
            <div
                v-for="(group, groupIndex) in terminalConfigStore.allSnippetScopes"
                :key="group.scope"
                class="scope-group"
            >
                <!-- Separator between groups -->
                <div v-if="groupIndex > 0" class="group-separator"></div>

                <!-- Group header -->
                <div class="group-header">
                    <span v-if="group.scope === 'global'" class="group-header-global">All projects</span>
                    <ProjectBadge v-else :project-id="projectIdFromScope(group.scope)" />
                </div>

                <!-- Snippets in this group -->
                <div class="snippet-list">
                    <div
                        v-for="(snippet, index) in group.snippets"
                        :key="index"
                        class="snippet-row"
                    >
                        <!-- Reorder arrows -->
                        <div class="reorder-arrows">
                            <button
                                class="reorder-btn"
                                :class="{ disabled: index === 0 }"
                                :disabled="index === 0"
                                @click="terminalConfigStore.reorderSnippet(group.scope, index, index - 1)"
                                title="Move up"
                            ><wa-icon name="chevron-up" /></button>
                            <button
                                class="reorder-btn"
                                :class="{ disabled: index === group.snippets.length - 1 }"
                                :disabled="index === group.snippets.length - 1"
                                @click="terminalConfigStore.reorderSnippet(group.scope, index, index + 1)"
                                title="Move down"
                            ><wa-icon name="chevron-down" /></button>
                        </div>

                        <!-- Display text -->
                        <div class="snippet-display">
                            <span class="snippet-label">{{ snippet.label }}</span>
                            <span class="snippet-text-preview">{{ snippet.text }}{{ snippet.appendEnter ? '↵' : '' }}</span>
                        </div>

                        <!-- Action buttons -->
                        <div class="snippet-actions">
                            <button class="action-btn" @click="openEditForm(group.scope, index)" title="Edit">
                                <wa-icon name="pen-to-square" />
                            </button>
                            <button class="action-btn" @click="openDuplicateForm(group.scope, index)" title="Duplicate">
                                <wa-icon name="copy" />
                            </button>
                            <button class="action-btn action-btn-danger" @click="terminalConfigStore.deleteSnippet(group.scope, index)" title="Delete">
                                <wa-icon name="trash-can" />
                            </button>
                        </div>
                    </div>
                </div>
            </div>

            <!-- Show message when there are no scopes at all (edge case) -->
            <div v-if="terminalConfigStore.allSnippetScopes.length === 0" class="empty-message">
                No snippets yet. Add one to get started.
            </div>
        </div>

        <!-- ═══ FORM VIEW ═══ -->
        <form v-else :id="formId" class="dialog-content" @submit.prevent="handleSave">
            <!-- Label field -->
            <div class="form-group">
                <label class="form-label">Label</label>
                <wa-input
                    ref="labelInputRef"
                    :value="formData.label"
                    @input="formData.label = $event.target.value"
                    placeholder='e.g. "git status"'
                    size="small"
                />
            </div>

            <!-- Text to send -->
            <div class="form-group">
                <label class="form-label">Text to send</label>
                <wa-textarea
                    :value="formData.text"
                    @input="formData.text = $event.target.value"
                    rows="3"
                    placeholder='e.g. "git status --short"'
                    size="small"
                    class="snippet-textarea"
                />
            </div>

            <!-- Append Enter checkbox + Scope dropdown on same row -->
            <div class="form-options-row">
                <!-- Append Enter -->
                <wa-checkbox
                    :checked="formData.appendEnter"
                    @change="formData.appendEnter = $event.target.checked"
                    size="small"
                >
                    Append Enter
                </wa-checkbox>

                <!-- Scope select -->
                <wa-select
                    :value="formData.scope"
                    @change="formData.scope = $event.target.value"
                    size="small"
                    class="scope-select"
                >
                    <span
                        v-if="formData.scope !== 'global'"
                        slot="start"
                        class="selected-project-dot"
                        :style="selectedScopeProjectColor ? { '--dot-color': selectedScopeProjectColor } : null"
                    ></span>
                    <wa-option value="global">All projects</wa-option>

                    <!-- Named projects (sorted by mtime desc) -->
                    <wa-divider v-if="namedProjects.length"></wa-divider>
                    <wa-option
                        v-for="p in namedProjects"
                        :key="p.id"
                        :value="`project:${p.id}`"
                        :label="dataStore.getProjectDisplayName(p.id)"
                    >
                        <ProjectBadge :project-id="p.id" />
                    </wa-option>

                    <!-- Unnamed projects (directory tree) -->
                    <wa-divider v-if="unnamedFlatTree.length"></wa-divider>
                    <template v-for="item in unnamedFlatTree" :key="item.key">
                        <wa-option
                            v-if="item.isFolder"
                            disabled
                            class="tree-folder-option"
                        >
                            <span class="tree-folder-label" :style="{ paddingLeft: `${item.depth * 12}px` }">
                                {{ item.segment }}
                            </span>
                        </wa-option>
                        <wa-option
                            v-else
                            :value="`project:${item.project.id}`"
                            :label="dataStore.getProjectDisplayName(item.project.id)"
                        >
                            <span :style="{ paddingLeft: `${item.depth * 12}px` }">
                                <ProjectBadge :project-id="item.project.id" />
                            </span>
                        </wa-option>
                    </template>
                </wa-select>
            </div>

            <!-- Warning (duplicate label) -->
            <wa-callout v-if="warningMessage" variant="warning" size="small">
                {{ warningMessage }}
            </wa-callout>

            <!-- Error -->
            <wa-callout v-if="errorMessage" variant="danger" size="small">
                {{ errorMessage }}
            </wa-callout>
        </form>

        <!-- ═══ FOOTER ═══ -->
        <div slot="footer" class="dialog-footer">
            <template v-if="view === 'list'">
                <wa-button variant="neutral" appearance="outlined" @click="close">
                    Close
                </wa-button>
                <wa-button variant="brand" @click="openAddForm">
                    + Add snippet
                </wa-button>
            </template>
            <template v-else>
                <wa-button variant="neutral" appearance="outlined" @click="cancelForm">
                    Cancel
                </wa-button>
                <wa-button ref="saveButtonRef" type="submit" variant="brand">
                    Save
                </wa-button>
            </template>
        </div>
    </wa-dialog>
</template>

<style scoped>
.manage-snippets-dialog {
    --width: min(40rem, calc(100vw - 2rem));
}

.dialog-content {
    display: flex;
    flex-direction: column;
    gap: var(--wa-space-m);
    button {
        box-shadow: none;
        margin: 0;
    }
}

/* ── Empty state ──────────────────────────────────────────────────── */
.empty-message {
    font-size: var(--wa-font-size-s);
    color: var(--wa-color-text-quiet);
    text-align: center;
    padding: var(--wa-space-l) 0;
}

/* ── Scope groups ─────────────────────────────────────────────────── */
.scope-group {
    display: flex;
    flex-direction: column;
    gap: var(--wa-space-3xs);
}

.group-separator {
    border-top: 1px solid var(--wa-color-border-base);
    margin: var(--wa-space-xs) 0;
}

.group-header {
    padding: var(--wa-space-2xs) var(--wa-space-s);
}

.group-header-global {
    font-size: var(--wa-font-size-xs);
    font-weight: var(--wa-font-weight-semibold);
    text-transform: uppercase;
    letter-spacing: 0.5px;
    color: var(--wa-color-text-quiet);
}

/* ── Snippet list ─────────────────────────────────────────────────── */
.snippet-list {
    display: flex;
    flex-direction: column;
    gap: var(--wa-space-3xs);
}

.snippet-row {
    display: flex;
    align-items: center;
    gap: var(--wa-space-s);
    background: var(--wa-color-surface-alt);
    border-radius: var(--wa-border-radius-m);
}

/* ── Reorder arrows ───────────────────────────────────────────────── */
.reorder-arrows {
    display: flex;
    gap: var(--wa-space-2xs);
    flex-shrink: 0;
}

.reorder-btn {
    background: none;
    border: none;
    color: var(--wa-color-text-quiet);
    font-size: var(--wa-font-size-xs);
    padding: var(--wa-space-2xs);
    cursor: pointer;
    transition: color 0.15s, background-color 0.15s;
}

.reorder-btn:hover:not(.disabled) {
    color: var(--wa-color-text-base);
    background: var(--wa-color-surface-alt);
}

.reorder-btn.disabled {
    opacity: 0.25;
    cursor: default;
}

/* ── Snippet display ──────────────────────────────────────────────── */
.snippet-display {
    flex: 1;
    min-width: 0;
    display: flex;
    flex-direction: column;
    gap: 1px;
}

.snippet-label {
    font-size: var(--wa-font-size-s);
    font-weight: var(--wa-font-weight-semibold);
    color: var(--wa-color-brand-text);
    white-space: nowrap;
    overflow: hidden;
    text-overflow: ellipsis;
}

.snippet-text-preview {
    font-size: var(--wa-font-size-xs);
    font-family: var(--wa-font-family-code);
    color: var(--wa-color-text-quiet);
    white-space: nowrap;
    overflow: hidden;
    text-overflow: ellipsis;
}

/* ── Action buttons ───────────────────────────────────────────────── */
.snippet-actions {
    display: flex;
    gap: var(--wa-space-3xs);
    flex-shrink: 0;
}

.action-btn {
    background: none;
    border: none;
    font-size: var(--wa-font-size-m);
    padding: var(--wa-space-xs);
    cursor: pointer;
    line-height: 1;
    transition: background-color 0.15s, color 0.15s;
    color: var(--wa-color-text-quiet);
}

.action-btn:hover {
    background: var(--wa-color-surface-alt);
    color: var(--wa-color-text-base);
}

.action-btn-danger:hover {
    color: var(--wa-color-danger-text);
}

/* ── Form ─────────────────────────────────────────────────────────── */
.form-group {
    display: flex;
    flex-direction: column;
    gap: var(--wa-space-xs);
}

.form-label {
    font-size: var(--wa-font-size-s);
    font-weight: var(--wa-font-weight-semibold);
}

.snippet-textarea::part(textarea) {
    font-family: var(--wa-font-family-code);
}

.form-options-row {
    display: flex;
    align-items: center;
    gap: var(--wa-space-m);
    flex-wrap: wrap;
}

.scope-select {
    margin-left: auto;
    min-width: 160px;
}

.selected-project-dot {
    width: 0.75em;
    height: 0.75em;
    border-radius: 50%;
    flex-shrink: 0;
    border: 1px solid;
    box-sizing: border-box;
    background-color: var(--dot-color, transparent);
    border-color: var(--dot-color, var(--wa-color-surface-border));
}

.tree-folder-label {
    font-family: var(--wa-font-family-code);
    font-size: var(--wa-font-size-s);
}

/* ── Footer ───────────────────────────────────────────────────────── */
.dialog-footer {
    display: flex;
    gap: var(--wa-space-s);
    justify-content: flex-end;
}
</style>
