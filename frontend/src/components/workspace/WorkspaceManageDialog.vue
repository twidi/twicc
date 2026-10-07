<script setup>
// WorkspaceManageDialog.vue - Dialog for managing workspaces (list + create/edit form)
import ReorderHandle from '../ui/ReorderHandle.vue'
import { moveVisibleItems } from '../../utils/listReorder'
import { ref, computed, nextTick, useId } from 'vue'
import { useWorkspacesStore } from '../../stores/workspaces'
import { useSettingsStore } from '../../stores/settings'
import { useHelpStore } from '../../stores/help'
import { useDataStore } from '../../stores/data'
import ProjectBadge from '../project/ProjectBadge.vue'
import ProjectSelectOptions from '../project/ProjectSelectOptions.vue'
import DirectoryPickerPopup from '../files/DirectoryPickerPopup.vue'
import { matchPattern } from '../../utils/workspacePatterns'
import BrowserUrlListEditor from '../browser/BrowserUrlListEditor.vue'

const workspacesStore = useWorkspacesStore()
const settingsStore = useSettingsStore()
const helpStore = useHelpStore()
const dataStore = useDataStore()

// -- Dialog refs --------------------------------------------------------------
const dialogRef = ref(null)
const saveButtonRef = ref(null)
const nameInputRef = ref(null)
const browserUrlsEditorRef = ref(null)

const instanceId = useId()
const formId = `manage-workspaces-form-${instanceId}`

// -- View state ---------------------------------------------------------------
const view = ref('list') // 'list' or 'form'
const errorMessage = ref('')
const deleteConfirmId = ref(null) // workspace ID pending delete confirmation
const localShowArchived = ref(false) // local toggle, independent from the global setting
const localShowArchivedProjects = ref(false) // local toggle for the projects list inside the form view
let dialogShown = false // true between the dialog's after-show and hide; gates focusing on list→form switches

// -- Form data (buffered until Save) -----------------------------------------
const formData = ref({
    id: null,          // null for create mode
    name: '',
    color: '',
    archived: false,
    projectIds: [],    // local copy, manipulated freely until save
    autoProjectPatterns: [],
    browserUrls: [],   // saved Browser-pane URL entries ({url, label?, default?})
})

// -- Pattern input state -----------------------------------------------------
const patternInput = ref('')
const scanFeedback = ref('')
let scanFeedbackTimer = null

// -- Computed -----------------------------------------------------------------
const dialogLabel = computed(() => {
    if (view.value === 'list') return 'Workspaces'
    if (formData.value.id) return 'Edit Workspace'
    return 'New Workspace'
})

/** Workspaces to display in the list, respecting the dialog-local "show archived" toggle. */
const visibleWorkspaces = computed(() => {
    const all = workspacesStore.getAllWorkspaces
    if (localShowArchived.value) return all
    return all.filter(w => !w.archived)
})

/** Projects available to add (not already in the form's projectIds, respecting the local archived toggle).
 *  Worktrees are excluded — they are subordinate to their main repository, not standalone workspace members. */
const availableProjects = computed(() => {
    const inSet = new Set(formData.value.projectIds)
    const showArchived = localShowArchivedProjects.value
    return dataStore.getListableProjects.filter(p => !inSet.has(p.id) && (showArchived || !p.archived))
})

/** Count of non-worktree projects in a workspace (worktrees don't count as projects). */
function listableProjectCount(workspace) {
    return workspace.projectIds.filter(pid => !dataStore.getProject(pid)?.worktree_of).length
}

/** Project entries to render in the form's project list, paired with their source index in formData.projectIds.
 *  Worktrees are never listed as standalone members (even if auto-added or added before), and the local
 *  "show archived" toggle can hide archived projects without removing them from the workspace. */
const visibleProjectEntries = computed(() => {
    const showArchived = localShowArchivedProjects.value
    return formData.value.projectIds
        .map((pid, index) => ({ pid, index }))
        .filter(({ pid }) => {
            const project = dataStore.getProject(pid)
            // Worktrees are subordinate to their main repo — never shown here.
            if (project?.worktree_of) return false
            if (showArchived) return true
            return !project?.archived
        })
})

// -- List view helpers --------------------------------------------------------
function handleReorder(fromIndex, toIndex) {
    const indices = visibleWorkspaces.value.map((workspace) => workspacesStore.workspaces.indexOf(workspace))
    workspacesStore.reorderWorkspace(fromIndex, toIndex, indices)
}

function requestDelete(workspaceId) {
    deleteConfirmId.value = workspaceId
}

function cancelDelete() {
    deleteConfirmId.value = null
}

function confirmDelete(workspaceId) {
    workspacesStore.deleteWorkspace(workspaceId)
    deleteConfirmId.value = null
}

// -- Form helpers -------------------------------------------------------------
function openAddForm() {
    formData.value = {
        id: null,
        name: '',
        color: '',
        archived: false,
        projectIds: [],
        autoProjectPatterns: [],
        browserUrls: [],
    }
    patternInput.value = ''
    scanFeedback.value = ''
    errorMessage.value = ''
    localShowArchivedProjects.value = settingsStore.isShowArchivedProjects
    view.value = 'form'
    nextTick(() => { syncFormState(); if (dialogShown) focusFirstInput() })
}

function openEditForm(workspace) {
    formData.value = {
        id: workspace.id,
        name: workspace.name,
        color: workspace.color || '',
        archived: workspace.archived,
        projectIds: [...workspace.projectIds],
        autoProjectPatterns: [...(workspace.autoProjectPatterns || [])],
        browserUrls: (workspace.browserUrls || []).map(e => ({ ...e })),
    }
    patternInput.value = ''
    scanFeedback.value = ''
    errorMessage.value = ''
    localShowArchivedProjects.value = settingsStore.isShowArchivedProjects
    view.value = 'form'
    nextTick(() => { syncFormState(); if (dialogShown) focusFirstInput() })
}

function cancelForm() {
    view.value = 'list'
    errorMessage.value = ''
}

// -- Project list manipulation (form) -----------------------------------------
function addProject(event) {
    const projectId = event.target.value
    if (!projectId) return
    if (!formData.value.projectIds.includes(projectId)) {
        formData.value.projectIds.push(projectId)
    }
    // Reset the select back to placeholder
    event.target.value = ''
}

function removeProject(visibleIndex) {
    const entries = visibleProjectEntries.value
    const realIdx = entries[visibleIndex]?.index
    if (realIdx === undefined) return
    formData.value.projectIds.splice(realIdx, 1)
}

/** Reorder visible projects without moving hidden archived projects or worktrees. */
function reorderProjects(from, to) {
    moveVisibleItems(formData.value.projectIds, visibleProjectEntries.value.map((entry) => entry.index), from, to)
}

// -- Pattern list manipulation (form) -----------------------------------------

/** Computed v-model for DirectoryPickerPopup: if patternInput has no *, pass it through;
 *  otherwise return '' so the picker opens at home. */
const pickerDirectory = computed({
    get() {
        return patternInput.value.includes('*') ? '' : patternInput.value
    },
    set(dir) {
        patternInput.value = dir
    },
})

function addPattern() {
    const trimmed = patternInput.value.trim()
    if (!trimmed) return
    if (!formData.value.autoProjectPatterns.includes(trimmed)) {
        formData.value.autoProjectPatterns.push(trimmed)
    }
    patternInput.value = ''
}

function removePattern(index) {
    formData.value.autoProjectPatterns.splice(index, 1)
}

function scanNow() {
    // Worktrees are never auto-added to a workspace by pattern.
    const projects = dataStore.getListableProjects
    const patterns = formData.value.autoProjectPatterns
    if (patterns.length === 0) {
        scanFeedback.value = 'No patterns defined'
        clearScanFeedbackLater()
        return
    }
    let added = 0
    for (const project of projects) {
        if (!project.directory || formData.value.projectIds.includes(project.id)) continue
        if (patterns.some(p => matchPattern(project.directory, p))) {
            formData.value.projectIds.push(project.id)
            added++
        }
    }
    scanFeedback.value = added > 0
        ? `${added} project${added > 1 ? 's' : ''} added`
        : 'No new projects found'
    clearScanFeedbackLater()
}

function clearScanFeedbackLater() {
    if (scanFeedbackTimer) clearTimeout(scanFeedbackTimer)
    scanFeedbackTimer = setTimeout(() => { scanFeedback.value = '' }, 4000)
}

// -- Validation & save --------------------------------------------------------
function handleSave() {
    errorMessage.value = ''

    const trimmedName = formData.value.name.trim()

    if (!trimmedName) {
        errorMessage.value = 'Name is required.'
        return
    }

    if (trimmedName.length > 20) {
        errorMessage.value = 'Name must be 20 characters or less.'
        return
    }

    // Uniqueness check (exclude self when editing)
    const isDuplicate = workspacesStore.getAllWorkspaces.some(w => {
        if (formData.value.id && w.id === formData.value.id) return false
        return w.name.trim().toLowerCase() === trimmedName.toLowerCase()
    })
    if (isDuplicate) {
        errorMessage.value = 'A workspace with this name already exists.'
        return
    }

    const { entries: browserUrls, error: browserUrlsError } =
        browserUrlsEditorRef.value?.getEntries() || { entries: [] }
    if (browserUrlsError) {
        errorMessage.value = browserUrlsError
        return
    }

    const payload = {
        name: trimmedName,
        color: formData.value.color || null,
        projectIds: [...formData.value.projectIds],
        archived: formData.value.archived,
        autoProjectPatterns: [...formData.value.autoProjectPatterns],
        browserUrls,
    }

    if (formData.value.id) {
        workspacesStore.updateWorkspace(formData.value.id, payload)
    } else {
        workspacesStore.createWorkspace(payload)
    }

    view.value = 'list'
    errorMessage.value = ''
}

// -- Dialog lifecycle ---------------------------------------------------------
function syncFormState() {
    nextTick(() => {
        if (saveButtonRef.value) {
            saveButtonRef.value.setAttribute('form', formId)
        }
    })
}

function focusFirstInput() {
    if (view.value === 'form' && nameInputRef.value) {
        nameInputRef.value.focus()
        const len = nameInputRef.value.value?.length || 0
        nameInputRef.value.setSelectionRange(len, len)
    }
}

// Guard dialog events against bubbling from child wa-select/wa-dropdown
function handleDialogShow(e) {
    if (e.target !== dialogRef.value) return
    syncFormState()
}

function handleDialogAfterShow(e) {
    if (e.target !== dialogRef.value) return
    dialogShown = true
    focusFirstInput()
}

function handleDialogHide(e) {
    if (e.target !== dialogRef.value) return
    dialogShown = false
}

// First time the user opens the workspace dialog (manage or create), surface
// the workspaces help with the dismiss switch. No-ops once seen.
function maybeShowWorkspaceHelp() {
    helpStore.maybeAutoShow('workspaces', {
        platform: settingsStore._isTouchDevice ? 'mobile' : 'desktop',
        os: settingsStore.os,
        enabledProviders: settingsStore.enabledProviders,
    })
}

function open() {
    maybeShowWorkspaceHelp()
    view.value = 'list'
    errorMessage.value = ''
    deleteConfirmId.value = null
    localShowArchived.value = settingsStore.isShowArchivedWorkspaces
    if (dialogRef.value) {
        dialogRef.value.open = true
    }
}

function close() {
    if (dialogRef.value) {
        dialogRef.value.open = false
    }
}

function openForWorkspace(workspaceId) {
    const ws = workspacesStore.getWorkspaceById(workspaceId)
    if (!ws) {
        open()
        return
    }
    maybeShowWorkspaceHelp()
    errorMessage.value = ''
    deleteConfirmId.value = null
    localShowArchived.value = settingsStore.isShowArchivedWorkspaces
    openEditForm(ws)
    if (dialogRef.value) {
        dialogRef.value.open = true
    }
}

function openNew() {
    maybeShowWorkspaceHelp()
    errorMessage.value = ''
    deleteConfirmId.value = null
    localShowArchived.value = settingsStore.isShowArchivedWorkspaces
    openAddForm()
    if (dialogRef.value) {
        dialogRef.value.open = true
    }
}

defineExpose({ open, close, openForWorkspace, openNew })
</script>

<template>
    <wa-dialog
        ref="dialogRef"
        :label="dialogLabel"
        class="manage-workspaces-dialog"
        @wa-show="handleDialogShow"
        @wa-after-show="handleDialogAfterShow"
        @wa-hide="handleDialogHide"
    >
        <!-- Custom header: title + (edit mode) a live badge preview of the
             workspace, reflecting the unsaved name/color being edited. -->
        <div slot="label" class="dialog-title">
            <span class="dialog-title-text">{{ dialogLabel }}</span>
            <span v-if="view === 'form' && formData.id" class="dialog-title-badge">
                <wa-icon name="layer-group" auto-width :style="formData.color ? { color: formData.color } : null"></wa-icon>
                <span class="dialog-title-badge-name">{{ formData.name.trim() || 'Workspace' }}</span>
            </span>
        </div>

        <!-- === LIST VIEW === -->
        <div v-if="view === 'list'" class="dialog-content">
            <!-- Show archived toggle (local to dialog, does not affect global setting) -->
            <div v-if="workspacesStore.hasArchivedWorkspaces" class="archived-toggle">
                <wa-switch
                    :checked="localShowArchived"
                    @change="localShowArchived = $event.target.checked"
                    size="small"
                >
                    Show archived workspaces
                </wa-switch>
            </div>

            <!-- Workspace list -->
            <div class="workspace-list" data-reorder-list>
                <div
                    v-for="(workspace, index) in visibleWorkspaces"
                    :key="workspace.id"
                    class="workspace-row" data-reorder-row
                >
                    <!-- Drag handle -->
                    <ReorderHandle
                        :index="index"
                        :count="visibleWorkspaces.length"
                        @reorder="handleReorder"
                    />

                    <!-- Display info -->
                    <div class="workspace-display">
                        <span class="workspace-name"><wa-icon name="layer-group" auto-width :style="workspace.color ? { color: workspace.color } : null"></wa-icon> {{ workspace.name }}</span>
                        <span class="workspace-project-count">
                            {{ listableProjectCount(workspace) }} project{{ listableProjectCount(workspace) !== 1 ? 's' : '' }}
                        </span>
                    </div>

                    <!-- Action buttons -->
                    <div class="workspace-actions">
                        <!-- Delete confirmation -->
                        <template v-if="deleteConfirmId === workspace.id">
                            <span class="delete-confirm-label">Delete?</span>
                            <button class="action-btn action-btn-confirm-danger" @click="confirmDelete(workspace.id)" title="Confirm delete">
                                <wa-icon name="check" />
                            </button>
                            <button class="action-btn" @click="cancelDelete" title="Cancel delete">
                                <wa-icon name="xmark" />
                            </button>
                        </template>
                        <template v-else>
                            <button class="action-btn" @click="openEditForm(workspace)" title="Edit">
                                <wa-icon name="pen-to-square" />
                            </button>
                            <button
                                class="action-btn"
                                @click="workspacesStore.updateWorkspace(workspace.id, { archived: !workspace.archived })"
                                :title="workspace.archived ? 'Unarchive' : 'Archive'"
                            >
                                <wa-icon :name="workspace.archived ? 'box-open' : 'box-archive'" :style="workspace.archived ? { color: 'var(--wa-color-warning-60)' } : null" />
                            </button>
                            <button class="action-btn action-btn-danger" @click="requestDelete(workspace.id)" title="Delete">
                                <wa-icon name="trash-can" />
                            </button>
                        </template>
                    </div>
                </div>
            </div>

            <!-- Empty state -->
            <div v-if="visibleWorkspaces.length === 0" class="empty-message">
                No workspaces yet. Create one to get started.
            </div>
        </div>

        <!-- === FORM VIEW === -->
        <form v-else :id="formId" class="dialog-content" @submit.prevent="handleSave">
            <!-- Name + color share one row -->
            <div class="name-color-row">
                <div class="form-group name-group">
                    <label class="form-label">Name</label>
                    <wa-input
                        ref="nameInputRef"
                        :value="formData.name"
                        @input="formData.name = $event.target.value"
                        placeholder="e.g. &quot;Frontend work&quot;"
                        size="small"
                        maxlength="20"
                    />
                </div>

                <div class="form-group color-group">
                    <label class="form-label">Color</label>
                    <wa-color-picker
                        :value.prop="formData.color"
                        @change="formData.color = $event.target.value"
                        size="small"
                    ></wa-color-picker>
                </div>
            </div>

            <wa-divider></wa-divider>

            <!-- Project list -->
            <div class="form-group">
                <div class="form-label-row">
                    <label class="form-label">Projects</label>
                    <wa-switch
                        :checked="localShowArchivedProjects"
                        @change="localShowArchivedProjects = $event.target.checked"
                        size="small"
                    >
                        Show archived
                    </wa-switch>
                </div>

                <div v-if="visibleProjectEntries.length > 0" class="project-list" data-reorder-list>
                    <div
                        v-for="(entry, visibleIndex) in visibleProjectEntries"
                        :key="entry.pid"
                        class="project-row" data-reorder-row
                    >
                        <!-- Drag handle -->
                        <ReorderHandle
                            :index="visibleIndex"
                            :count="visibleProjectEntries.length"
                            @reorder="reorderProjects"
                        />

                        <!-- Project badge -->
                        <div class="project-row-badge">
                            <ProjectBadge :project-id="entry.pid" />
                        </div>

                        <!-- Remove button -->
                        <button
                            type="button"
                            class="action-btn action-btn-danger"
                            @click="removeProject(visibleIndex)"
                            title="Remove project"
                        >
                            <wa-icon name="xmark" />
                        </button>
                    </div>
                </div>

                <div v-else-if="formData.projectIds.length > 0" class="empty-projects-message">
                    All projects in this workspace are archived (hidden).
                </div>

                <div v-else class="empty-projects-message">
                    No projects added yet.
                </div>

                <!-- Add project select -->
                <wa-select
                    v-if="availableProjects.length > 0"
                    value=""
                    @change="addProject"
                    placeholder="Add a project..."
                    size="small"
                    class="add-project-select"
                >
                    <ProjectSelectOptions :projects="availableProjects" />
                </wa-select>
            </div>

            <wa-divider></wa-divider>

            <!-- Auto-add project patterns -->
            <div class="form-group">
                <label class="form-label">Auto-add project patterns</label>
                <p class="form-help-text">
                    New projects whose directory matches a pattern will be added automatically when detected.
                    <br>Use <code>*</code> as wildcard. A plain directory matches all projects inside it.
                    <br>To add existing matching projects, click <strong>Add matching projects now</strong>.
                </p>

                <!-- Existing patterns -->
                <div v-if="formData.autoProjectPatterns.length > 0" class="pattern-list">
                    <div
                        v-for="(pattern, index) in formData.autoProjectPatterns"
                        :key="index"
                        class="pattern-row"
                    >
                        <span class="pattern-value">{{ pattern }}</span>
                        <button
                            type="button"
                            class="action-btn action-btn-danger"
                            @click="removePattern(index)"
                            title="Remove pattern"
                        >
                            <wa-icon name="xmark" />
                        </button>
                    </div>
                </div>

                <!-- Add pattern input -->
                <div class="pattern-input-row">
                    <wa-input
                        :value="patternInput"
                        @input="patternInput = $event.target.value"
                        @keydown.enter.prevent="addPattern"
                        placeholder="e.g. /home/user/projects/*"
                        size="small"
                        class="pattern-input"
                    />
                    <DirectoryPickerPopup v-model="pickerDirectory" />
                    <wa-button
                        type="button"
                        variant="neutral"
                        appearance="outlined"
                        size="small"
                        @click="addPattern"
                        :disabled="!patternInput.trim()"
                    >
                        Add
                    </wa-button>
                </div>

                <!-- Scan now -->
                <div class="scan-row">
                    <wa-button
                        type="button"
                        variant="neutral"
                        size="small"
                        @click="scanNow"
                    >
                        <wa-icon name="magnifying-glass-plus" slot="start"></wa-icon>
                        Add matching projects now
                    </wa-button>
                    <span v-if="scanFeedback" class="scan-feedback">{{ scanFeedback }}</span>
                </div>
            </div>

            <wa-divider></wa-divider>

            <!-- Browser pane saved URLs -->
            <div class="form-group">
                <label class="form-label">Browser URLs</label>
                <p class="form-help-text">
                    URLs saved for the session Browser tab of this workspace's
                    projects; the selected one is the Home default. A project's
                    own saved URLs take precedence.
                </p>
                <BrowserUrlListEditor
                    ref="browserUrlsEditorRef"
                    :entries="formData.browserUrls"
                />
            </div>

            <!-- Error -->
            <wa-callout v-if="errorMessage" variant="danger" size="small">
                {{ errorMessage }}
            </wa-callout>
        </form>

        <!-- === FOOTER === -->
        <div slot="footer" class="dialog-footer">
            <template v-if="view === 'list'">
                <wa-button variant="neutral" appearance="outlined" @click="close">
                    Close
                </wa-button>
                <wa-button variant="brand" @click="openAddForm">
                    <wa-icon name="plus" slot="start"></wa-icon>
                    New workspace
                </wa-button>
            </template>
            <template v-else>
                <wa-switch
                    :checked="formData.archived"
                    @change="formData.archived = $event.target.checked"
                    size="small"
                    class="footer-archived-switch"
                >
                    Archived
                </wa-switch>
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
.manage-workspaces-dialog {
    --width: min(36rem, calc(100vw - 2rem));
}

/* Dialog header: title on the left, the live workspace badge on the right.
   The gap is the minimum separation kept when a long title leaves no free
   space (otherwise margin-left:auto pushes the badge to the right edge). */
.dialog-title {
    display: flex;
    align-items: center;
    gap: var(--wa-space-m);
    flex-wrap: wrap;
    min-width: 0;
}

/* Live badge preview pinned to the right of the title — icon + name, a touch
   smaller. inline-flex/gap lay it out; min/max-width keep it ellipsized. */
.dialog-title-badge {
    display: inline-flex;
    align-items: center;
    gap: var(--wa-space-xs);
    margin-left: auto;
    font-size: 0.85em;
    min-width: 0;
    max-width: 100%;
}

.dialog-title-badge-name {
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
    min-width: 0;
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

/* -- Archived toggle -------------------------------------------------------- */
.archived-toggle {
    padding-bottom: var(--wa-space-2xs);
}

/* -- Empty state ------------------------------------------------------------ */
.empty-message {
    font-size: var(--wa-font-size-s);
    color: var(--wa-color-text-quiet);
    text-align: center;
    padding: var(--wa-space-l) 0;
}

.empty-projects-message {
    font-size: var(--wa-font-size-s);
    color: var(--wa-color-text-quiet);
    padding: var(--wa-space-xs) 0;
}

/* -- Workspace list --------------------------------------------------------- */
.workspace-list {
    display: flex;
    flex-direction: column;
    gap: var(--wa-space-3xs);
}

.workspace-row {
    display: flex;
    align-items: center;
    gap: var(--wa-space-s);
    background: var(--wa-color-surface-alt);
    border-radius: var(--wa-border-radius-m);
}

/* -- Workspace display ------------------------------------------------------ */
.workspace-display {
    flex: 1;
    min-width: 0;
    display: flex;
    flex-wrap: wrap;
    align-items: center;
    gap: var(--wa-space-xs);
}

.workspace-name {
    font-size: var(--wa-font-size-s);
    font-weight: var(--wa-font-weight-semibold);
    color: var(--wa-color-brand-text);
    white-space: nowrap;
    overflow: hidden;
    text-overflow: ellipsis;
    > wa-icon {
        margin-right: var(--wa-space-2xs);
    }
}

.archived-tag {
    font-size: var(--wa-font-size-2xs);
    color: var(--wa-color-text-quiet);
    background: var(--wa-color-surface-base);
    border: 1px solid var(--wa-color-border-quiet);
    border-radius: var(--wa-border-radius-s);
    padding: 0 var(--wa-space-2xs);
    white-space: nowrap;
    line-height: 1.6;
}

.workspace-project-count {
    font-size: var(--wa-font-size-xs);
    color: var(--wa-color-text-quiet);
    white-space: nowrap;
    margin-left: auto;
}

/* -- Action buttons --------------------------------------------------------- */
.workspace-actions {
    display: flex;
    align-items: center;
    gap: var(--wa-space-3xs);
    flex-shrink: 0;
}

.delete-confirm-label {
    font-size: var(--wa-font-size-xs);
    color: var(--wa-color-danger-60);
    white-space: nowrap;
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
    color: var(--wa-color-danger-60);
}

/* The delete-confirmation check stays red at rest and on hover (destructive confirm). */
.action-btn-confirm-danger,
.action-btn-confirm-danger:hover {
    color: var(--wa-color-danger-60);
}

/* -- Form ------------------------------------------------------------------- */
.form-group {
    display: flex;
    flex-direction: column;
    gap: var(--wa-space-xs);
}

.form-label {
    font-size: var(--wa-font-size-s);
    font-weight: var(--wa-font-weight-semibold);
}

/* Name + color side by side: name takes the remaining width, color hugs its swatch. */
.name-color-row {
    display: flex;
    align-items: flex-start;
    gap: var(--wa-space-m);
}

.name-color-row .name-group {
    flex: 1;
    min-width: 0;
}

.name-color-row .color-group {
    flex: 0 0 auto;
}

.form-label-row {
    display: flex;
    align-items: center;
    justify-content: space-between;
    gap: var(--wa-space-s);
}

/* -- Project list in form --------------------------------------------------- */
.project-list {
    display: flex;
    flex-direction: column;
    gap: var(--wa-space-3xs);
}

.project-row {
    display: flex;
    align-items: center;
    gap: var(--wa-space-s);
    background: var(--wa-color-surface-alt);
    border-radius: var(--wa-border-radius-m);
    padding: var(--wa-space-3xs) 0;
}

.project-row-badge {
    flex: 1;
    min-width: 0;
    overflow: hidden;
}

.add-project-select {
    max-width: 280px;
}

/* -- Pattern list in form -------------------------------------------------- */
.pattern-list {
    display: flex;
    flex-direction: column;
    gap: var(--wa-space-3xs);
}

.pattern-row {
    display: flex;
    align-items: center;
    gap: var(--wa-space-s);
    background: var(--wa-color-surface-alt);
    border-radius: var(--wa-border-radius-m);
    padding: var(--wa-space-3xs) var(--wa-space-xs);
}

.pattern-value {
    flex: 1;
    min-width: 0;
    font-size: var(--wa-font-size-s);
    font-family: var(--wa-font-family-mono);
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
}

.pattern-input-row {
    display: flex;
    align-items: center;
    gap: var(--wa-space-xs);
    flex-wrap: wrap;
}

.pattern-input {
    flex: 1;
    min-width: 9rem;
}

.pattern-input-row > wa-button {
    margin-left: auto;
}

.form-help-text {
    font-size: var(--wa-font-size-xs);
    color: var(--wa-color-text-quiet);
    margin: 0;
    line-height: 1.4;
    code {
        font-family: var(--wa-font-family-mono);
        background: var(--wa-color-surface-alt);
        padding: 0 var(--wa-space-3xs);
        border-radius: var(--wa-border-radius-s);
    }
}

.scan-row {
    display: flex;
    align-items: center;
    gap: var(--wa-space-s);
}

.scan-feedback {
    font-size: var(--wa-font-size-s);
    color: var(--wa-color-text-quiet);
}

/* -- Footer ----------------------------------------------------------------- */
.dialog-footer {
    display: flex;
    flex-wrap: wrap;
    gap: var(--wa-space-s);
    justify-content: flex-end;
    width: 100%;
    align-items: center;
}

.footer-archived-switch {
    margin-right: auto;
}
</style>
