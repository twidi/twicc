<script setup>
// MessageSnippetsDialog.vue - Dialog for managing message input snippets with scope grouping
import GroupedListEditor from '../ui/GroupedListEditor.vue'
import { isSnippetGroup, getGroupItems, flattenGroupedEntries } from '../../utils/snippetGroups'
import { ref, computed, nextTick, useId, watch } from 'vue'
import { useRoute } from 'vue-router'
import { useMessageSnippetsStore } from '../../stores/messageSnippets'
import { useDataStore } from '../../stores/data'
import { useWorkspacesStore } from '../../stores/workspaces'
import ProjectBadge from '../project/ProjectBadge.vue'
import ProjectMark from '../project/ProjectMark.vue'
import { buildProjectTree, flattenProjectTree } from '../../utils/projectTree'
import { splitProjectsByPriority } from '../../utils/projectSort'
import { PLACEHOLDERS, extractPlaceholders } from '../../utils/snippetPlaceholders'

const props = defineProps({
    currentProjectId: {
        type: String,
        default: null,
    },
})

const route = useRoute()
const messageSnippetsStore = useMessageSnippetsStore()
const dataStore = useDataStore()
const workspacesStore = useWorkspacesStore()

// ── Dialog refs ──────────────────────────────────────────────────────
const dialogRef = ref(null)
const saveButtonRef = ref(null)
const labelInputRef = ref(null)
const textareaRef = ref(null)

const instanceId = useId()
const formId = `message-snippets-form-${instanceId}`

// ── View state ───────────────────────────────────────────────────────
const view = ref('list') // 'list' or 'form'
const editScope = ref(null)    // scope being edited (null for new)
const editSourceGroupId = ref(null)
const editSourceSnapshot = ref(null)
const groupMode = ref(false)
const editIndex = ref(null)    // index within scope (null for new)
const isDuplicate = ref(false)
const formData = ref(null)     // { label: '', text: '', scope: 'global' }
const errorMessage = ref('')
const warningMessage = ref('')

// ── Computed ─────────────────────────────────────────────────────────
const dialogLabel = computed(() => {
    if (groupMode.value && view.value === 'form') return 'Add Group'
    if (view.value === 'list') return 'Manage Message Snippets'
    if (editIndex.value !== null) return 'Edit Snippet'
    return 'Add Snippet'
})

/** Active projects (non-stale, non-archived, worktrees excluded) — same filter as sidebar. */
const activeProjects = computed(() =>
    dataStore.getListableProjects.filter(p => !p.stale && !p.archived)
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

/** Active workspace project IDs (ordered), or null when no workspace is active. */
const activeWsProjectIds = computed(() => {
    const wsId = route.query.workspace
    return wsId ? workspacesStore.getVisibleProjectIds(wsId) : null
})

const activeWs = computed(() => {
    const wsId = route.query.workspace
    return wsId ? workspacesStore.getWorkspaceById(wsId) : null
})

const activeWsLabel = computed(() => activeWs.value ? `${activeWs.value.name} projects` : null)
const activeWsColor = computed(() => activeWs.value?.color || null)

/** When a workspace is active, split named projects into prioritized and others. */
const namedSplit = computed(() =>
    splitProjectsByPriority(namedProjects.value, activeWsProjectIds.value)
)

/** When a workspace is active, split unnamed projects into prioritized and others. */
const unnamedSplit = computed(() => {
    const unnamed = activeProjects.value.filter(p => p.name === null)
    return splitProjectsByPriority(unnamed, activeWsProjectIds.value)
})

/** Prioritized unnamed projects as a flat tree (for the scope selector). */
const prioritizedUnnamedFlatTree = computed(() => {
    if (!unnamedSplit.value.prioritized.length) return []
    const roots = buildProjectTree(unnamedSplit.value.prioritized)
    return flattenProjectTree(roots)
})

/** Non-prioritized unnamed projects as a flat tree (for the scope selector). */
const othersUnnamedFlatTree = computed(() => {
    if (!activeWsProjectIds.value) return unnamedFlatTree.value
    if (!unnamedSplit.value.others.length) return []
    const roots = buildProjectTree(unnamedSplit.value.others)
    return flattenProjectTree(roots)
})

/**
 * Project IDs in display order: named projects by mtime desc, then unnamed sorted by directory.
 * Used to order scope groups in the list view.
 */
const orderedProjectIds = computed(() => [
    ...namedProjects.value.map(p => p.id),
    ...activeProjects.value
        .filter(p => p.name === null)
        .sort((a, b) => (a.directory || '').localeCompare(b.directory || ''))
        .map(p => p.id),
])

/** Active workspace ID from the route. */
const activeWorkspaceId = computed(() => route.query.workspace || null)

/** Ordered snippet scopes for the list view. */
const snippetScopes = computed(() =>
    messageSnippetsStore.allSnippetScopes(
        props.currentProjectId,
        orderedProjectIds.value,
        activeWorkspaceId.value,
        activeWsProjectIds.value,
    )
)

/** All selectable workspaces for the scope selector (current workspace first). */
const selectableWorkspaces = computed(() => {
    const all = workspacesStore.getSelectableWorkspaces
    if (!activeWorkspaceId.value) return all
    const current = all.find(ws => ws.id === activeWorkspaceId.value)
    const others = all.filter(ws => ws.id !== activeWorkspaceId.value)
    return current ? [current, ...others] : others
})

/** Color of the currently selected project scope (for the dot in the closed select). */
const selectedScopeProjectColor = computed(() => {
    if (!formData.value) return null
    const scope = formData.value.scope
    if (scope === 'global' || scope.startsWith('workspace:')) return null
    const pid = scope.slice('project:'.length)
    const project = dataStore.getProject(pid)
    return project?.color || null
})

/** Icon URL of the currently selected project scope (else null → color dot). */
const selectedScopeProjectIconUrl = computed(() => {
    if (!formData.value) return null
    const scope = formData.value.scope
    if (scope === 'global' || scope.startsWith('workspace:')) return null
    return dataStore.resolvedProjectIcons[scope.slice('project:'.length)] || null
})

/** Whether the current scope is a workspace, and its color. */
const isScopeWorkspace = computed(() => formData.value?.scope?.startsWith('workspace:') || false)
const selectedScopeWorkspaceColor = computed(() => {
    if (!isScopeWorkspace.value) return null
    return workspaceColorFromScope(formData.value.scope)
})

const selectableGroups = computed(() =>
    (messageSnippetsStore.snippets[formData.value?.scope] || []).filter(isSnippetGroup)
)

watch(() => formData.value?.scope, () => {
    if (formData.value?.groupId && !selectableGroups.value.some(group => group.id === formData.value.groupId)) {
        formData.value.groupId = null
    }
})

function openGroupForm() {
    openAddForm()
    groupMode.value = true
    nextTick(() => labelInputRef.value?.focus())
}

// ── Form helpers ─────────────────────────────────────────────────────
function openAddForm(scope = null, groupId = null) {
    groupMode.value = false
    editSourceGroupId.value = null
    editSourceSnapshot.value = null
    editScope.value = null
    editIndex.value = null
    isDuplicate.value = false
    formData.value = {
        label: '',
        text: '',
        scope: scope || (props.currentProjectId ? `project:${props.currentProjectId}` : 'global'),
        groupId,
    }
    errorMessage.value = ''
    warningMessage.value = ''
    view.value = 'form'
    nextTick(() => syncFormState())
}

function openEditForm(scope, index, groupId = null) {
    groupMode.value = false
    editSourceGroupId.value = groupId
    editScope.value = scope
    editIndex.value = index
    isDuplicate.value = false
    const snippet = getGroupItems(messageSnippetsStore.snippets[scope] || [], groupId)?.[index]
    if (!snippet) return
    editSourceSnapshot.value = JSON.stringify(snippet)
    formData.value = {
        label: snippet.label,
        text: snippet.text,
        scope: scope,
        groupId,
    }
    errorMessage.value = ''
    warningMessage.value = ''
    view.value = 'form'
    nextTick(() => syncFormState())
}

function openDuplicateForm(scope, index, groupId = null) {
    groupMode.value = false
    editSourceGroupId.value = null
    editSourceSnapshot.value = null
    editScope.value = null
    editIndex.value = null
    isDuplicate.value = true
    const snippet = getGroupItems(messageSnippetsStore.snippets[scope] || [], groupId)?.[index]
    if (!snippet) return
    formData.value = {
        label: snippet.label,
        text: snippet.text,
        scope: scope,
        groupId,
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

/** Extract workspace ID from a scope string like "workspace:xxx" */
function workspaceIdFromScope(scope) {
    return scope.startsWith('workspace:') ? scope.slice('workspace:'.length) : null
}

/** Get workspace name for display in scope group headers. */
function workspaceNameFromScope(scope) {
    const wsId = workspaceIdFromScope(scope)
    if (!wsId) return null
    const ws = workspacesStore.getWorkspaceById(wsId)
    return ws ? ws.name : wsId
}

/** Get workspace color from a scope string. */
function workspaceColorFromScope(scope) {
    const wsId = workspaceIdFromScope(scope)
    if (!wsId) return null
    const ws = workspacesStore.getWorkspaceById(wsId)
    return ws?.color || null
}

/** Display text for a snippet in the list: label if set, otherwise the full text on one line. */
function snippetDisplayLabel(snippet) {
    return snippet.label || null
}

// ── Textarea insertion helpers ──────────────────────────────────────
function insertAtCursor(insertValue) {
    const textarea = textareaRef.value?.shadowRoot?.querySelector('textarea')
    const start = textarea?.selectionStart ?? formData.value.text.length
    const end = textarea?.selectionEnd ?? formData.value.text.length
    const current = formData.value.text
    formData.value.text = current.slice(0, start) + insertValue + current.slice(end)
    const newPos = start + insertValue.length
    nextTick(() => {
        if (textarea) {
            textarea.focus()
            textarea.setSelectionRange(newPos, newPos)
        }
    })
}

function insertPlaceholder(id) {
    insertAtCursor(`{${id}}`)
}

// ── Validation & save ────────────────────────────────────────────────
function handleSave() {
    errorMessage.value = ''

    const trimmedLabel = formData.value.label.trim()
    if (groupMode.value) {
        if (!trimmedLabel) {
            errorMessage.value = 'Group name is required.'
            return
        }
        if (!messageSnippetsStore.addSnippetGroup(formData.value.scope, trimmedLabel)) {
            errorMessage.value = 'This item or group changed. Reopen the item and try again.'
            return
        }
        cancelForm()
        return
    }
    if (editIndex.value !== null) {
        const source = getGroupItems(messageSnippetsStore.snippets[editScope.value] || [], editSourceGroupId.value)?.[editIndex.value]
        if (!source || JSON.stringify(source) !== editSourceSnapshot.value) {
            errorMessage.value = 'This item or group changed. Reopen the item and try again.'
            return
        }
    }
    if (formData.value.groupId && !selectableGroups.value.some(group => group.id === formData.value.groupId)) {
        errorMessage.value = 'The selected group no longer exists. Select another group.'
        return
    }
    const text = formData.value.text

    if (!text.trim()) {
        errorMessage.value = 'Message text is required.'
        return
    }

    const selectedScope = formData.value.scope
    const snippetData = {
        label: trimmedLabel,
        text: text,
        placeholders: extractPlaceholders(text),
    }

    // Check for duplicate label in same scope (warn but allow save on second submit)
    if (trimmedLabel) {
        const scopeSnippets = flattenGroupedEntries(messageSnippetsStore.snippets[selectedScope] || [])
        const sourceSnippet = getGroupItems(messageSnippetsStore.snippets[editScope.value] || [], editSourceGroupId.value)?.[editIndex.value]
        const hasDuplicateLabel = scopeSnippets.some(s => {
            // Skip self when editing within the same scope
            if (editIndex.value !== null && editScope.value === selectedScope && s === sourceSnippet) return false
            return s.label && s.label.trim().toLowerCase() === trimmedLabel.toLowerCase()
        })
        if (hasDuplicateLabel && !warningMessage.value) {
            warningMessage.value = 'A snippet with the same label already exists in this scope. Submit again to save anyway.'
            return
        }
    }
    warningMessage.value = ''

    // Save
    const saved = editIndex.value !== null
        ? messageSnippetsStore.updateSnippet(
            editScope.value, editIndex.value, snippetData, selectedScope,
            editSourceGroupId.value, formData.value.groupId,
        )
        : messageSnippetsStore.addSnippet(selectedScope, snippetData, formData.value.groupId)
    if (!saved) {
        errorMessage.value = 'This item or group changed. Reopen the item and try again.'
        return
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
    if (view.value === 'form' && groupMode.value) {
        labelInputRef.value?.focus()
    } else if (view.value === 'form') {
        // Focus the textarea (message text) since label is optional
        if (textareaRef.value) {
            const inner = textareaRef.value.shadowRoot?.querySelector('textarea')
            if (inner) inner.focus()
        }
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
        class="message-snippets-dialog"
        @wa-show="handleDialogShow"
        @wa-after-show="handleDialogAfterShow"
        @wa-hide="handleDialogHide"
    >
        <!-- ═══ LIST VIEW ═══ -->
        <div v-if="view === 'list'" class="dialog-content">
            <div
                v-for="(group, groupIndex) in snippetScopes"
                :key="group.scope"
                class="scope-group"
            >
                <!-- Separator between groups -->
                <div v-if="groupIndex > 0" class="group-separator"></div>

                <!-- Group header -->
                <div class="group-header">
                    <span v-if="group.scope === 'global'" class="group-header-global">All projects</span>
                    <span v-else-if="group.scope.startsWith('workspace:')" class="group-header-workspace">
                        <wa-icon name="layer-group" auto-width :style="workspaceColorFromScope(group.scope) ? { color: workspaceColorFromScope(group.scope) } : null"></wa-icon>
                        Workspace {{ workspaceNameFromScope(group.scope) }}
                    </span>
                    <ProjectBadge v-else :project-id="projectIdFromScope(group.scope)" use-directory-for-unnamed />
                </div>

                <GroupedListEditor
                    :entries="group.snippets"
                    item-name="snippet"
                    @add="groupId => openAddForm(group.scope, groupId)"
                    @edit="(index, groupId) => openEditForm(group.scope, index, groupId)"
                    @duplicate="(index, groupId) => openDuplicateForm(group.scope, index, groupId)"
                    @delete="(index, groupId) => messageSnippetsStore.deleteSnippet(group.scope, index, groupId)"
                    @move="(from, to) => messageSnippetsStore.moveSnippet(group.scope, from, to)"
                    @add-group="label => messageSnippetsStore.addSnippetGroup(group.scope, label)"
                    @rename-group="(id, label) => messageSnippetsStore.renameSnippetGroup(group.scope, id, label)"
                    @delete-group="id => messageSnippetsStore.deleteSnippetGroup(group.scope, id)"
                >
                    <template #item="{ entry: snippet }">
                        <div class="snippet-display">
                            <span v-if="snippetDisplayLabel(snippet)" class="snippet-label">{{ snippetDisplayLabel(snippet) }}</span>
                            <span class="snippet-text-preview">{{ snippet.text }}</span>
                        </div>
                    </template>
                </GroupedListEditor>
            </div>

            <!-- Show message when there are no scopes at all -->
            <div v-if="snippetScopes.length === 0" class="empty-message">
                No snippets yet. Add one to get started.
            </div>
        </div>

        <!-- ═══ FORM VIEW ═══ -->
        <form v-else :id="formId" class="dialog-content" @submit.prevent="handleSave">
            <!-- Label field (optional) -->
            <div class="form-group">
                <label class="form-label">{{ groupMode ? 'Group name' : 'Label' }} <span v-if="!groupMode" class="form-optional">(optional)</span></label>
                <wa-input
                    ref="labelInputRef"
                    :value="formData.label"
                    @input="formData.label = $event.target.value"
                    :placeholder="groupMode ? 'e.g. Reviews' : 'e.g. &quot;Quick fix request&quot;'"
                    size="small"
                />
            </div>

            <!-- Message text -->
            <div v-if="!groupMode" class="form-group">
                <label class="form-label">Message</label>
                <wa-textarea
                    ref="textareaRef"
                    :value="formData.text"
                    @input="formData.text = $event.target.value"
                    placeholder="The message text to insert..."
                    rows="4"
                    resize="auto"
                    size="small"
                    class="message-textarea"
                />

                <!-- Placeholder picker -->
                <p class="placeholder-hint">Insert placeholders to be resolved at insert time:</p>
                <div class="placeholder-picker-row">
                    <button
                        v-for="p in PLACEHOLDERS"
                        :key="p.id"
                        type="button"
                        class="picker-key placeholder-key"
                        @click="insertPlaceholder(p.id)"
                        :title="`Insert {${p.id}}`"
                    >{{ p.label }}</button>
                </div>
            </div>

            <!-- Scope select -->
            <div class="form-group">
                <label class="form-label">Scope</label>
                <wa-select
                    :value="formData.scope"
                    @change="formData.scope = $event.target.value"
                    size="small"
                    class="scope-select"
                >
                    <wa-icon
                        v-if="isScopeWorkspace"
                        slot="start"
                        name="layer-group"
                        :style="selectedScopeWorkspaceColor ? { color: selectedScopeWorkspaceColor } : null"
                    ></wa-icon>
                    <ProjectMark
                        v-else-if="formData.scope !== 'global'"
                        slot="start"
                        :icon-url="selectedScopeProjectIconUrl"
                        :color="selectedScopeProjectColor"
                    />
                    <wa-option value="global">All projects</wa-option>

                    <!-- Workspaces -->
                    <template v-if="selectableWorkspaces.length">
                        <wa-divider></wa-divider>
                        <wa-option disabled class="section-header-option">Workspaces</wa-option>
                        <wa-option
                            v-for="ws in selectableWorkspaces"
                            :key="ws.id"
                            :value="`workspace:${ws.id}`"
                            :label="ws.name"
                        >
                            <wa-icon name="layer-group" auto-width :style="{ marginRight: '0.5em', opacity: 0.6, ...(ws.color ? { color: ws.color, opacity: 1 } : {}) }"></wa-icon>
                            {{ ws.name }}
                        </wa-option>
                    </template>

                    <!-- Divider before projects when not in a workspace -->
                    <template v-if="!activeWsLabel">
                        <wa-divider></wa-divider>
                        <wa-option v-if="selectableWorkspaces.length" disabled class="section-header-option">Projects</wa-option>
                    </template>

                    <!-- Workspace-prioritized projects (only when workspace is active) -->
                    <template v-if="namedSplit.prioritized.length || prioritizedUnnamedFlatTree.length">
                        <wa-divider></wa-divider>
                        <wa-option disabled class="section-header-option"><wa-icon name="layer-group" auto-width class="ws-header-icon" :style="activeWsColor ? { color: activeWsColor } : null"></wa-icon> {{ activeWsLabel }}</wa-option>
                    </template>
                    <wa-option
                        v-for="p in namedSplit.prioritized"
                        :key="p.id"
                        :value="`project:${p.id}`"
                        :label="dataStore.getProjectDisplayName(p.id)"
                    >
                        <ProjectBadge :project-id="p.id" />
                    </wa-option>

                    <!-- Workspace-prioritized unnamed projects (directory tree) -->
                    <wa-divider v-if="prioritizedUnnamedFlatTree.length"></wa-divider>
                    <template v-for="item in prioritizedUnnamedFlatTree" :key="item.key">
                        <wa-option
                            v-if="item.isFolder"
                            disabled
                            class="tree-folder-option"
                        >
                            <span class="tree-folder-label" :title="item.path" :style="{ paddingLeft: `${item.depth * 12}px` }">
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

                    <!-- Remaining projects -->
                    <template v-if="activeWsLabel && (namedSplit.others.length || othersUnnamedFlatTree.length)">
                        <wa-divider></wa-divider>
                        <wa-option disabled class="section-header-option">Other projects</wa-option>
                    </template>
                    <wa-option
                        v-for="p in namedSplit.others"
                        :key="p.id"
                        :value="`project:${p.id}`"
                        :label="dataStore.getProjectDisplayName(p.id)"
                    >
                        <ProjectBadge :project-id="p.id" />
                    </wa-option>

                    <!-- Remaining unnamed projects (directory tree) -->
                    <wa-divider v-if="othersUnnamedFlatTree.length"></wa-divider>
                    <template v-for="item in othersUnnamedFlatTree" :key="item.key">
                        <wa-option
                            v-if="item.isFolder"
                            disabled
                            class="tree-folder-option"
                        >
                            <span class="tree-folder-label" :title="item.path" :style="{ paddingLeft: `${item.depth * 12}px` }">
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

            <div v-if="!groupMode" class="form-group">
                <label class="form-label">Group</label>
                <wa-select :value="formData.groupId || ''" @change="formData.groupId = $event.target.value || null" size="small">
                    <wa-option value="">None</wa-option>
                    <wa-option v-for="group in selectableGroups" :key="group.id" :value="group.id">{{ group.label }}</wa-option>
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
                <wa-button variant="neutral" appearance="outlined" @click="openGroupForm">
                    <wa-icon slot="start" name="folder-plus"></wa-icon>
                    Add group
                </wa-button>
                <wa-button variant="brand" @click="openAddForm()">
                    <wa-icon slot="start" name="plus"></wa-icon>
                    Add snippet
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
.message-snippets-dialog {
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

.group-header-workspace {
    display: inline-flex;
    align-items: center;
    gap: var(--wa-space-xs);
    font-size: var(--wa-font-size-s);
    font-weight: var(--wa-font-weight-semibold);
    color: var(--wa-color-text-normal);
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

.form-optional {
    font-weight: normal;
    color: var(--wa-color-text-quiet);
}

.message-textarea::part(textarea) {
    font-family: var(--wa-font-family-code);
}

/* ── Placeholder picker ──────────────────────────────────────────── */
.placeholder-hint {
    font-size: var(--wa-font-size-xs);
    color: var(--wa-color-text-quiet);
    margin: 0;
}

.placeholder-picker-row {
    display: flex;
    gap: var(--wa-space-2xs);
    flex-wrap: wrap;
}

.picker-key {
    background: var(--wa-color-surface-raised);
    border: 1px solid var(--wa-color-surface-border);
    color: var(--wa-color-text-normal);
    font-size: var(--wa-font-size-s);
    height: 1.75rem;
    min-width: 2rem;
    padding: 0 var(--wa-space-xs);
    display: inline-flex;
    align-items: center;
    justify-content: center;
    border-radius: var(--wa-border-radius-s);
    cursor: pointer;
    transition: background-color 0.1s, border-color 0.1s;
    user-select: none;
}

.picker-key:hover {
    background: color-mix(in srgb, var(--wa-color-surface-raised), var(--wa-color-mix-hover));
}

.picker-key:active {
    background: color-mix(in srgb, var(--wa-color-surface-raised), var(--wa-color-mix-active));
    transform: scale(0.95);
}

.placeholder-key {
    font-family: var(--wa-font-family-sans) !important;
    font-size: var(--wa-font-size-s) !important;
    padding: 0 var(--wa-space-xs) !important;
    border-color: var(--wa-color-brand-border-quiet) !important;
    color: var(--wa-color-brand-on-quiet) !important;
}

.placeholder-key:hover {
    background: var(--wa-color-brand-fill-quiet) !important;
}

/* ── Scope select ─────────────────────────────────────────────────── */
.scope-select {
    min-width: 160px;
}

.tree-folder-label {
    font-family: var(--wa-font-family-code);
    font-size: var(--wa-font-size-s);
}

.section-header-option {
    font-size: var(--wa-font-size-xs);
    font-weight: var(--wa-font-weight-semibold);
    text-transform: uppercase;
    letter-spacing: 0.05em;
    color: var(--wa-color-text-quiet);
}

.ws-header-icon {
    font-size: var(--wa-font-size-s);
    color: var(--wa-color-text-normal);
    margin-inline: 0.2em;
}

/* ── Footer ───────────────────────────────────────────────────────── */
.dialog-footer {
    display: flex;
    gap: var(--wa-space-s);
    justify-content: flex-end;
}
</style>
