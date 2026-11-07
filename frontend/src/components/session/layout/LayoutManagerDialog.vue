<script setup>
// The single home for managing the named-layouts catalog: rename + delete. Opened from both the
// settings "Layouts" section and the main-area layout menu (one reusable surface — the rename/delete
// logic lives here only). Visual language (action buttons, inline "Delete?" confirmation, icons)
// mirrors WorkspaceManageDialog. Deleting a layout that is referenced by the global default or by any
// project's default offers a single reassignment target before removal; resolution is
// dangling-tolerant regardless, so the reassignment is cleanup, not correctness.
import { ref, computed, nextTick } from 'vue'
import { useLayoutsStore, SINGLE_PANE_ID, SINGLE_PANE_NAME } from '../../../stores/layouts'
import { useSettingsStore } from '../../../stores/settings'
import { useDataStore } from '../../../stores/data'
import { apiFetch } from '../../../utils/api'

const layoutsStore = useLayoutsStore()
const settingsStore = useSettingsStore()
const dataStore = useDataStore()

const dialogRef = ref(null)
const renameInputRef = ref(null)

// At most one row is in rename or delete mode at a time.
const editingId = ref(null)
const editingName = ref('')
const editError = ref('')

const deletingId = ref(null)
const deleteRefs = ref({ global: false, projects: [] }) // references to the layout being deleted
// Sentinel for the "Auto" reassignment choice. Maps to null (inherit from parent) for projects /
// worktrees; the global default can't inherit, so it falls back to single pane instead.
const INHERIT = '__inherit__'
const reassignTarget = ref(SINGLE_PANE_ID)
const deleteError = ref('')
const isDeleting = ref(false)

const namedLayouts = computed(() => layoutsStore.getAllLayouts)

// Reassignment options for a deletion: single pane first, then — only when projects / worktrees
// reference the layout — the "Auto" inherit choice, then every other named layout (never the one
// going away). "Auto" is omitted when only the global default references the layout: the global
// default has no parent scope to inherit from.
const reassignOptions = computed(() => [
    { id: SINGLE_PANE_ID, name: SINGLE_PANE_NAME },
    ...(deleteRefs.value.projects.length ? [{ id: INHERIT, name: 'Auto (inherited from parent scope)' }] : []),
    ...namedLayouts.value.filter((l) => l.id !== deletingId.value),
])

const hasReferences = computed(() => deleteRefs.value.global || deleteRefs.value.projects.length > 0)

// Which scopes currently point at a given layout id (global default + projects).
function referencesFor(id) {
    const global = (settingsStore.getDefaultLayoutId || SINGLE_PANE_ID) === id
    const projects = Object.values(dataStore.projects).filter((p) => p.default_layout_id === id)
    return { global, projects: projects.map((p) => p.id) }
}

function resetRowState() {
    editingId.value = null
    editingName.value = ''
    editError.value = ''
    deletingId.value = null
    deleteError.value = ''
    isDeleting.value = false
}

// --- Rename ---------------------------------------------------------------

function startRename(layout) {
    resetRowState()
    editingId.value = layout.id
    editingName.value = layout.name
    nextTick(() => {
        const input = renameInputRef.value
        if (!input) return
        input.focus()
        const len = input.value?.length || 0
        input.setSelectionRange?.(len, len)
    })
}

function cancelRename() {
    editingId.value = null
    editingName.value = ''
    editError.value = ''
}

function commitRename() {
    const id = editingId.value
    const trimmed = editingName.value.trim()
    if (!trimmed) {
        editError.value = 'Name cannot be empty.'
        return
    }
    const dup = namedLayouts.value.some(
        (l) => l.id !== id && l.name.trim().toLowerCase() === trimmed.toLowerCase(),
    )
    if (dup) {
        editError.value = `A layout named "${trimmed}" already exists.`
        return
    }
    layoutsStore.renameLayout(id, trimmed)
    cancelRename()
}

// --- Delete ---------------------------------------------------------------

function startDelete(layout) {
    resetRowState()
    deletingId.value = layout.id
    deleteRefs.value = referencesFor(layout.id)
    reassignTarget.value = SINGLE_PANE_ID
}

function cancelDelete() {
    deletingId.value = null
    deleteError.value = ''
    isDeleting.value = false
}

async function confirmDelete() {
    const id = deletingId.value
    if (!id) return
    deleteError.value = ''

    // Reassign references first (best path = nothing dangling); only remove once they succeed.
    if (hasReferences.value) {
        const target = reassignTarget.value
        // "Auto": projects clear their explicit default (null → inherit from parent); the global
        // default, which can't inherit, falls back to single pane.
        const projectTarget = target === INHERIT ? null : target
        const globalTarget = target === INHERIT ? SINGLE_PANE_ID : target
        isDeleting.value = true
        try {
            await Promise.all(
                deleteRefs.value.projects.map(async (projectId) => {
                    const res = await apiFetch(`/api/projects/${projectId}/`, {
                        method: 'PUT',
                        headers: { 'Content-Type': 'application/json' },
                        body: JSON.stringify({ default_layout_id: projectTarget }),
                    })
                    if (!res.ok) throw new Error('project update failed')
                    dataStore.updateProject(await res.json())
                }),
            )
        } catch {
            deleteError.value = 'Could not reassign all projects. Please retry.'
            isDeleting.value = false
            return
        }
        if (deleteRefs.value.global) settingsStore.setDefaultLayoutId(globalTarget)
    }

    layoutsStore.deleteLayout(id)
    cancelDelete()
}

// --- Dialog lifecycle -----------------------------------------------------

// Guard against WA custom events bubbling from nested wa-select / wa-input children.
function onDialogHide(e) {
    if (e.target !== dialogRef.value) return
    resetRowState()
}

function open() {
    resetRowState()
    if (dialogRef.value) dialogRef.value.open = true
}

function close() {
    if (dialogRef.value) dialogRef.value.open = false
}

defineExpose({ open, close })
</script>

<template>
    <wa-dialog
        ref="dialogRef"
        :label="$t('session.layout.manage.title')"
        class="layout-manager-dialog"
        @wa-hide="onDialogHide"
    >
        <div class="dialog-content">
            <p v-if="!namedLayouts.length" class="empty-message">
                {{ $t('session.layout.manage.empty') }}
            </p>

            <div v-else class="layout-list">
                <div v-for="l in namedLayouts" :key="l.id" class="layout-entry">
                    <div class="layout-row">
                        <!-- Rename mode -->
                        <template v-if="editingId === l.id">
                            <wa-input
                                :ref="(el) => (renameInputRef = el)"
                                class="rename-input"
                                :value.prop="editingName"
                                size="small"
                                :placeholder="$t('session.layout.namePlaceholder')"
                                @input="editingName = $event.target.value"
                                @keydown.enter.prevent="commitRename"
                                @keydown.escape.prevent="cancelRename"
                            ></wa-input>
                            <div class="layout-actions">
                                <button class="action-btn action-btn-confirm-ok" @click="commitRename" :title="$t('session.layout.manage.saveName')">
                                    <wa-icon name="check" />
                                </button>
                                <button class="action-btn" @click="cancelRename" :title="$t('session.layout.manage.cancelRename')">
                                    <wa-icon name="xmark" />
                                </button>
                            </div>
                        </template>

                        <!-- Normal display + actions -->
                        <template v-else>
                            <span class="layout-name">{{ l.name }}</span>
                            <div class="layout-actions">
                                <!-- Delete confirmation (inline, mirrors WorkspaceManageDialog) -->
                                <template v-if="deletingId === l.id">
                                    <span class="delete-confirm-label">{{ $t('session.layout.manage.deleteConfirm') }}</span>
                                    <button
                                        class="action-btn action-btn-confirm-danger"
                                        :disabled="isDeleting"
                                        @click="confirmDelete"
                                        :title="$t('session.layout.manage.confirmDelete')"
                                    ><wa-icon name="check" /></button>
                                    <button
                                        class="action-btn"
                                        :disabled="isDeleting"
                                        @click="cancelDelete"
                                        :title="$t('session.layout.manage.cancelDelete')"
                                    ><wa-icon name="xmark" /></button>
                                </template>
                                <template v-else>
                                    <button class="action-btn" @click="startRename(l)" :title="$t('common.actions.rename')">
                                        <wa-icon name="pen-to-square" />
                                    </button>
                                    <button
                                        class="action-btn action-btn-danger"
                                        @click="startDelete(l)"
                                        :title="$t('common.actions.delete')"
                                    ><wa-icon name="trash-can" /></button>
                                </template>
                            </div>
                        </template>
                    </div>

                    <!-- Rename error -->
                    <wa-callout
                        v-if="editingId === l.id && editError"
                        variant="danger"
                        size="small"
                    >{{ editError }}</wa-callout>

                    <!-- Reassignment, shown only when deleting a referenced layout. The inline
                         "Delete?" confirmation stays above; here we just collect the target. -->
                    <div v-if="deletingId === l.id && hasReferences" class="reassign-panel">
                        <span class="reassign-text">
                            {{ deleteRefs.global
                                ? (deleteRefs.projects.length
                                    ? $t('session.layout.manage.reassignGlobalProjects', { count: deleteRefs.projects.length }, deleteRefs.projects.length)
                                    : $t('session.layout.manage.reassignGlobal'))
                                : $t('session.layout.manage.reassignProjects', { count: deleteRefs.projects.length }, deleteRefs.projects.length) }}
                        </span>
                        <wa-select
                            class="reassign-select"
                            :value.prop="reassignTarget"
                            size="small"
                            @change="reassignTarget = $event.target.value"
                        >
                            <wa-option
                                v-for="o in reassignOptions"
                                :key="o.id"
                                :value="o.id"
                                :label="o.name"
                            >{{ o.name }}</wa-option>
                        </wa-select>
                        <wa-callout v-if="deleteError" variant="danger" size="small">{{ deleteError }}</wa-callout>
                    </div>
                </div>
            </div>
        </div>

        <div slot="footer" class="dialog-footer">
            <wa-button variant="neutral" appearance="outlined" @click="close">{{ $t('common.actions.close') }}</wa-button>
        </div>
    </wa-dialog>
</template>

<style scoped>
.layout-manager-dialog {
    --width: min(32rem, calc(100vw - 2rem));
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

.empty-message {
    font-size: var(--wa-font-size-s);
    color: var(--wa-color-text-quiet);
    text-align: center;
    padding: var(--wa-space-l) 0;
    margin: 0;
}

/* -- Layout list ------------------------------------------------------------ */
.layout-list {
    display: flex;
    flex-direction: column;
    gap: var(--wa-space-3xs);
}

.layout-entry {
    display: flex;
    flex-direction: column;
    gap: var(--wa-space-2xs);
}

.layout-row {
    display: flex;
    align-items: center;
    gap: var(--wa-space-s);
    background: var(--wa-color-surface-alt);
    border-radius: var(--wa-border-radius-m);
    padding-inline-start: var(--wa-space-s);
}

.layout-name {
    flex: 1;
    min-width: 0;
    font-size: var(--wa-font-size-s);
    font-weight: var(--wa-font-weight-semibold);
    color: var(--wa-color-brand-text);
    white-space: nowrap;
    overflow: hidden;
    text-overflow: ellipsis;
}

.rename-input {
    flex: 1;
    min-width: 0;
    margin-block: var(--wa-space-3xs);
}

/* -- Action buttons (mirror WorkspaceManageDialog) -------------------------- */
.layout-actions {
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

.action-btn:hover:not(:disabled) {
    background: var(--wa-color-surface-alt);
    color: var(--wa-color-text-base);
}

.action-btn-danger:hover:not(:disabled) {
    color: var(--wa-color-danger-60);
}

/* Confirmation check buttons keep their semantic color at rest and on hover:
   green for a benign validation (rename), red for a destructive confirm (delete). */
.action-btn-confirm-ok,
.action-btn-confirm-ok:hover:not(:disabled) {
    color: var(--wa-color-success-60);
}

.action-btn-confirm-danger,
.action-btn-confirm-danger:hover:not(:disabled) {
    color: var(--wa-color-danger-60);
}

.action-btn:disabled {
    opacity: 0.5;
    cursor: default;
}

/* -- Reassignment panel ----------------------------------------------------- */
.reassign-panel {
    display: flex;
    flex-direction: column;
    align-items: flex-end;
    gap: var(--wa-space-xs);
    padding: var(--wa-space-xs) var(--wa-space-s) var(--wa-space-s);
}

.reassign-text {
    font-size: var(--wa-font-size-s);
    color: var(--wa-color-text-quiet);
}

.reassign-select {
    max-width: 260px;
}

.dialog-footer {
    display: flex;
    justify-content: flex-end;
    width: 100%;
}
</style>
