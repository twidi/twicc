<script setup>
// ProjectEditDialog.vue - Dialog for editing/creating projects
import { ref, computed, watch, nextTick, useId } from 'vue'
import { useDataStore } from '../../stores/data'
import { useWorkspacesStore } from '../../stores/workspaces'
import { useSettingsStore } from '../../stores/settings'
import { useHelpStore } from '../../stores/help'
import { useLayoutsStore } from '../../stores/layouts'
import { apiFetch } from '../../utils/api'
import { matchPattern } from '../../utils/workspacePatterns'
import BrowserUrlListEditor from '../browser/BrowserUrlListEditor.vue'
import DirectoryPickerPopup from '../files/DirectoryPickerPopup.vue'
import { expandWorktreeTemplate } from '../../utils/worktreePath'
import { resolveProjectTrust } from '../../utils/trust'
import { useProjectDirectoryRecheck } from '../../composables/useProjectDirectoryRecheck'
import { resolveProjectIconUrl } from '../../utils/projectIcon'
import ProjectAgentDefaultsSection from './ProjectAgentDefaultsSection.vue'
import TabBar from '../ui/TabBar.vue'
import HelpIconButton from '../help/HelpIconButton.vue'
import AppTooltip from '../ui/AppTooltip.vue'
import ProjectBadge from './ProjectBadge.vue'
import ProjectDirectoryPath from './ProjectDirectoryPath.vue'
import ProjectMark from './ProjectMark.vue'

const props = defineProps({
    project: {
        type: Object,
        default: null,
    },
})

const emit = defineEmits(['saved'])

const store = useDataStore()
const workspacesStore = useWorkspacesStore()
const settingsStore = useSettingsStore()
const helpStore = useHelpStore()
const layoutsStore = useLayoutsStore()

// Refs for the dialog and form elements
const dialogRef = ref(null)
const directoryInputRef = ref(null)
const nameInputRef = ref(null)
const colorPickerRef = ref(null)
const saveButtonRef = ref(null)
const agentDefaultsRef = ref(null)
// Active main tab ('general' | 'agent'), controlled so we can reset to the
// Project tab on every open. Kept in sync with user clicks via @wa-tab-show
// (safe because the inner per-provider tab-group stops its own tab events).
const mainTab = ref('general')

// Local state for form values
const localDirectory = ref('')
const localName = ref('')
const localColor = ref('')
const localArchived = ref(false)
// Per-project default layout (edit mode): the picker holds '__inherit__' (= NULL) or a layout id.
const LAYOUT_INHERIT = '__inherit__'
const localDefaultLayoutId = ref(LAYOUT_INHERIT)
const selectableLayouts = computed(() => layoutsStore.selectableLayouts)
// Per-project saved Browser-pane URLs (edit mode): the list editor owns the
// editable rows; we reset it on (re)open and read it back at save time.
const browserUrlsEditorRef = ref(null)
// Absolute base directory for this project's git worktrees (edit mode, git
// projects only). Empty = inherit the global worktreeDirectoryTemplate.
const localWorktreeDirectory = ref('')
// Trust (edit mode): 'inherit' | 'trusted' | 'untrusted' + propagation flag.
const localTrustChoice = ref('inherit')
const localTrustPropagation = ref(false)
const isSaving = ref(false)
const errorMessage = ref('')
const directoryNotFound = ref(false)

// Workspace membership (buffered until Save).
// `selectedWorkspaceIds` is the working selection; `initialWorkspaceIds` is the
// snapshot at open time, used in edit mode to compute add/remove deltas so a
// workspace auto-added (or hidden behind the archived toggle) while the dialog
// is open is never removed by accident.
const selectedWorkspaceIds = ref([])
let initialWorkspaceIds = []
const localShowArchivedWorkspaces = ref(false)
const workspaceScanFeedback = ref('')
let workspaceScanFeedbackTimer = null

// Mode detection
const isCreateMode = computed(() => !props.project)
// Unique form ID per instance to avoid conflicts when multiple dialog instances coexist in the DOM
const instanceId = useId()
const formId = `project-dialog-form-${instanceId}`
const iconHelpId = `project-icon-help-${instanceId}`

// -- Live header badge --------------------------------------------------------
// Edit mode shows a live preview of the project's badge next to the title,
// reflecting the unsaved name/color the user is typing.

// Leaf folder name of the project's directory — the "unnamed" display fallback
// (same rule as the store's display name and worktreeLabel).
const directoryLeaf = computed(() => {
    const dir = (props.project?.directory || '').replace(/\/+$/, '')
    const idx = dir.lastIndexOf('/')
    return idx >= 0 ? dir.slice(idx + 1) : dir
})

// Badge label: the typed name, falling back to the directory leaf when empty —
// matching how an unnamed project (or worktree) renders once saved.
const previewName = computed(() => localName.value.trim() || directoryLeaf.value)

// -- Missing directory --------------------------------------------------------
// `stale` is a STORED observation ("the directory was gone last time TwiCC
// looked"), refreshed at startup and by the provider-folder watcher — never
// re-checked while rendering. Nothing watches the working directories, so a
// directory restored while TwiCC runs stays flagged until the "Re-check"
// button (or a restart) clears it.
const directoryMissing = computed(() => !isCreateMode.value && !!props.project?.stale)
const {
    busy: recheckBusy,
    outcome: recheckOutcome,
    recheck,
    reset: resetRecheck,
} = useProjectDirectoryRecheck()

function recheckDirectory() {
    recheck(props.project?.id)
}

// -- Trust --------------------------------------------------------------------
function trustChoiceFromValue(trust) {
    if (trust === true) return 'trusted'
    if (trust === false) return 'untrusted'
    return 'inherit'
}

// Effective trust resolved from the projects in the store, shown when the choice
// is "inherit" (no own decision) so the user sees what it falls back to.
const resolvedTrust = computed(() =>
    props.project ? resolveProjectTrust(props.project.id, store.projects) : null
)
const resolvedSourceName = computed(() => {
    const id = resolvedTrust.value?.sourceId
    if (!id || id === props.project?.id) return null
    const p = store.getProject(id)
    return p?.name || p?.directory || id
})

// -- Workspace membership -----------------------------------------------------
const allWorkspaces = computed(() => workspacesStore.getAllWorkspaces)
const hasArchivedWorkspaces = computed(() => allWorkspaces.value.some(w => w.archived))

/** Workspace IDs that currently list the given project (archived included). */
function workspaceIdsContaining(projectId) {
    return allWorkspaces.value.filter(ws => ws.projectIds.includes(projectId)).map(ws => ws.id)
}

function workspaceName(id) {
    return workspacesStore.getWorkspaceById(id)?.name || id
}

function workspaceColor(id) {
    return workspacesStore.getWorkspaceById(id)?.color || ''
}

/** Selected workspaces to render, paired with their source index, filtered by
 *  the local "show archived" toggle (and dropping ids whose workspace no longer
 *  exists). */
const selectedWorkspaceEntries = computed(() => {
    const showArchived = localShowArchivedWorkspaces.value
    return selectedWorkspaceIds.value
        .map((id, index) => ({ id, index }))
        .filter(({ id }) => {
            const ws = workspacesStore.getWorkspaceById(id)
            if (!ws) return false
            return showArchived || !ws.archived
        })
})

/** Workspaces available to add (not already selected, respecting the toggle). */
const availableWorkspaces = computed(() => {
    const inSet = new Set(selectedWorkspaceIds.value)
    const showArchived = localShowArchivedWorkspaces.value
    return allWorkspaces.value.filter(ws => !inSet.has(ws.id) && (showArchived || !ws.archived))
})

function addWorkspace(event) {
    const id = event.target.value
    if (!id) return
    if (!selectedWorkspaceIds.value.includes(id)) {
        selectedWorkspaceIds.value.push(id)
    }
    // Reset the select back to placeholder
    event.target.value = ''
}

function removeWorkspace(visibleIndex) {
    const entries = selectedWorkspaceEntries.value
    const realIdx = entries[visibleIndex]?.index
    if (realIdx === undefined) return
    selectedWorkspaceIds.value.splice(realIdx, 1)
}

/** Create mode only: add every workspace whose auto-add pattern matches the
 *  chosen directory. A preview of what the backend auto-add will do anyway. */
function scanMatchingWorkspaces() {
    const dir = localDirectory.value.trim()
    if (!dir) {
        workspaceScanFeedback.value = 'Enter a directory first'
        clearWorkspaceScanFeedbackLater()
        return
    }
    let added = 0
    for (const ws of allWorkspaces.value) {
        const patterns = ws.autoProjectPatterns || []
        if (!patterns.length || selectedWorkspaceIds.value.includes(ws.id)) continue
        if (patterns.some(p => matchPattern(dir, p))) {
            selectedWorkspaceIds.value.push(ws.id)
            added++
        }
    }
    workspaceScanFeedback.value = added > 0
        ? `${added} workspace${added > 1 ? 's' : ''} added`
        : 'No matching workspaces found'
    clearWorkspaceScanFeedbackLater()
}

function clearWorkspaceScanFeedbackLater() {
    if (workspaceScanFeedbackTimer) clearTimeout(workspaceScanFeedbackTimer)
    workspaceScanFeedbackTimer = setTimeout(() => { workspaceScanFeedback.value = '' }, 4000)
}

/** Reset the workspace section for the current mode. Called from open(). */
function resetWorkspaceState() {
    initialWorkspaceIds = isCreateMode.value ? [] : workspaceIdsContaining(props.project.id)
    selectedWorkspaceIds.value = [...initialWorkspaceIds]
    localShowArchivedWorkspaces.value = settingsStore.isShowArchivedWorkspaces
    workspaceScanFeedback.value = ''
    if (workspaceScanFeedbackTimer) {
        clearTimeout(workspaceScanFeedbackTimer)
        workspaceScanFeedbackTimer = null
    }
}

// Sync form values when project changes
watch(
    () => props.project,
    (newProject) => {
        if (newProject) {
            // Edit mode: use actual name (null for unnamed projects, not the display name)
            localName.value = newProject.name || ''
            localColor.value = newProject.color || ''
            localArchived.value = newProject.archived || false
            localWorktreeDirectory.value = newProject.worktree_directory || ''
            localTrustChoice.value = trustChoiceFromValue(newProject.trust)
            localTrustPropagation.value = newProject.trust_propagation ?? false
            localDefaultLayoutId.value = newProject.default_layout_id || LAYOUT_INHERIT
            browserUrlsEditorRef.value?.reset()
        } else {
            // Create mode: reset all fields
            localDirectory.value = ''
            localName.value = ''
            localColor.value = ''
            localArchived.value = false
            localWorktreeDirectory.value = ''
            localTrustChoice.value = 'inherit'
            localTrustPropagation.value = false
            localDefaultLayoutId.value = LAYOUT_INHERIT
        }
    },
    { immediate: true }
)

/**
 * Set form attribute on save button when dialog opens.
 * wa-button doesn't expose `form` as a property, so we must use setAttribute.
 */
function syncFormState() {
    nextTick(() => {
        if (saveButtonRef.value) {
            saveButtonRef.value.setAttribute('form', formId)
        }
    })
}

/**
 * Focus the first input after the dialog opening animation completes.
 * In create mode: focus the directory input.
 * In edit mode: focus the name input with cursor at end.
 */
function focusFirstInput() {
    if (isCreateMode.value) {
        const input = directoryInputRef.value
        if (!input) return
        input.focus()
    } else {
        const input = nameInputRef.value
        if (!input) return
        input.focus()
        // Move cursor to end of text
        const len = input.value?.length || 0
        input.setSelectionRange(len, len)
    }
}

// Guard the dialog's show/after-show handlers against events bubbling up from
// child wa-select / wa-dropdown (their dropdowns emit the same wa-show /
// wa-after-show custom events). Without this, opening the Workspaces select
// would run focusFirstInput(), steal focus from the dropdown, and close it.
function handleDialogShow(e) {
    if (e.target !== dialogRef.value) return
    syncFormState()
}

function handleDialogAfterShow(e) {
    if (e.target !== dialogRef.value) return
    focusFirstInput()
}

/**
 * Open the dialog.
 */
function open() {
    errorMessage.value = ''
    directoryNotFound.value = false
    mainTab.value = 'general'
    // Discard any staged icon change / scan state from a previous open.
    pendingIcon.value = null
    iconError.value = ''
    scanDone.value = false
    scanCandidates.value = []
    scanError.value = ''
    resetRecheck()
    if (isCreateMode.value) {
        localDirectory.value = ''
        localName.value = ''
        localColor.value = ''
        // First time the user opens the create-project dialog, surface the
        // projects help with the dismiss switch. No-ops once seen.
        helpStore.maybeAutoShow('projects', {
            platform: settingsStore._isTouchDevice ? 'mobile' : 'desktop',
            os: settingsStore.os,
            enabledProviders: settingsStore.enabledProviders,
        })
    } else if (props.project) {
        // Reset to current project values (handles reopen for same project
        // where the watch on props.project won't fire)
        localName.value = props.project.name || ''
        localColor.value = props.project.color || ''
        localArchived.value = props.project.archived || false
        localWorktreeDirectory.value = props.project.worktree_directory || ''
        localTrustChoice.value = trustChoiceFromValue(props.project.trust)
        localTrustPropagation.value = props.project.trust_propagation ?? false
        localDefaultLayoutId.value = props.project.default_layout_id || LAYOUT_INHERIT
        browserUrlsEditorRef.value?.reset()
        agentDefaultsRef.value?.reset()
    }
    resetWorkspaceState()
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
 * Handle directory input change.
 */
function onDirectoryInput(event) {
    localDirectory.value = event.target.value
}

// Reset the "directory not found" prompt when the path changes (manual input or picker)
watch(localDirectory, () => {
    if (directoryNotFound.value) {
        directoryNotFound.value = false
        errorMessage.value = ''
    }
})

/**
 * Handle name input change.
 */
function onNameInput(event) {
    localName.value = event.target.value
}

/**
 * Handle worktree directory input change.
 */
function onWorktreeDirectoryInput(event) {
    localWorktreeDirectory.value = event.target.value
}

// Worktree directory section visibility: edit mode, git projects only, and
// only for main repositories — never for worktree projects (worktree_of set),
// which inherit their main repo's setting rather than carrying their own.
const showWorktreeDirectory = computed(
    () => !isCreateMode.value && !!props.project?.git_root && !props.project?.worktree_of
)

// The global worktree directory template expanded against this project
// (resolving placeholders and any "../"). Empty when not in edit mode, the
// project is not under git, or no global template is set — in which case the
// "use the default" button is hidden. The concrete (placeholder-free) result is
// what gets stored as this project's worktree_directory.
const composedGlobalWorktreeDir = computed(() => {
    if (isCreateMode.value || !props.project?.git_root) return ''
    return expandWorktreeTemplate(settingsStore.getWorktreeDirectoryTemplate || '', props.project)
})

/**
 * Fill the worktree directory field with the expanded global default. The user
 * stays free to edit it afterwards or type any other absolute path.
 */
function useGlobalWorktreeDir() {
    if (composedGlobalWorktreeDir.value) {
        localWorktreeDirectory.value = composedGlobalWorktreeDir.value
    }
}

/**
 * Handle color picker change.
 */
function onColorChange(event) {
    localColor.value = event.target.value
}

// ---- Project icon ------------------------------------------------------
// Icon changes are STAGED locally and committed only on Save (like name/color),
// and discarded if the dialog closes without saving. `pendingIcon`:
//   null                   -> no change
//   { type: 'set', image } -> a new icon (data URI), from upload or scan
//   { type: 'none' }       -> switch to the color dot
//   { type: 'inherit' }    -> follow the inherited icon
const iconFileInputRef = ref(null)
const iconError = ref('')
const pendingIcon = ref(null)

// Effective staged state: 'inherit' | 'none' | 'override'.
const stagedIcon = computed(() => {
    if (pendingIcon.value) {
        return pendingIcon.value.type === 'set' ? 'override' : pendingIcon.value.type
    }
    const v = props.project?.icon || 'inherit'
    return v === 'inherit' ? 'inherit' : v === 'none' ? 'none' : 'override'
})

// Preview reflects the STAGED change (or the current effective icon).
const iconPreviewUrl = computed(() => {
    if (pendingIcon.value?.type === 'set') return pendingIcon.value.image
    if (pendingIcon.value?.type === 'none') return null
    if (pendingIcon.value?.type === 'inherit') {
        // What it would inherit: resolve as if this project had no override.
        const p = props.project
        if (!p) return null
        const patched = { ...store.projects, [p.id]: { ...p, icon: 'inherit', icon_override_url: null } }
        return resolveProjectIconUrl(p.id, patched)
    }
    return props.project ? (store.resolvedProjectIcons[props.project.id] || null) : null
})

// Scanning needs a git tree: available when the project resolves to a git root
// (own git_root, or an icon_anchor for a worktree / umbrella project).
const repoAnchorAvailable = computed(
    () => !isCreateMode.value && !!(props.project?.icon_anchor || props.project?.git_root)
)
const iconIsOverridden = computed(() => stagedIcon.value !== 'inherit')
const iconShowsColor = computed(() => stagedIcon.value === 'none')

function pickIconFile() {
    iconError.value = ''
    iconFileInputRef.value?.click()
}

function onIconFileChange(event) {
    const file = event.target?.files?.[0]
    if (event.target) event.target.value = '' // allow re-picking the same file
    if (!file) return
    if (file.size > 5 * 1024 * 1024) {
        iconError.value = 'Image too large (max 5 MB).'
        return
    }
    const reader = new FileReader()
    reader.onload = () => { pendingIcon.value = { type: 'set', image: reader.result }; iconError.value = '' }
    reader.onerror = () => { iconError.value = 'Could not read the file.' }
    reader.readAsDataURL(file)
}

// Stage a state change (applied on Save, not immediately).
function stageIcon(type) {
    iconError.value = ''
    pendingIcon.value = { type } // 'none' | 'inherit'
}

// Commit the staged icon change to the backend. Called from handleSave.
// Returns true on success (or when there is nothing to commit).
async function commitPendingIcon() {
    if (!pendingIcon.value || !props.project) return true
    const body = pendingIcon.value.type === 'set'
        ? { action: 'set', image: pendingIcon.value.image }
        : { action: pendingIcon.value.type }
    let response
    try {
        response = await apiFetch(`/api/projects/${props.project.id}/icon/`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(body),
        })
    } catch (error) {
        iconError.value = 'Network error while saving the icon.'
        return false
    }
    if (!response.ok) {
        const data = await response.json().catch(() => ({}))
        iconError.value = data.error || 'Icon update failed.'
        return false
    }
    store.updateProject(await response.json())
    pendingIcon.value = null
    return true
}

// Scan the repository for icon candidates (read-only) and let the user pick one.
const scanBusy = ref(false)
const scanDone = ref(false)
const scanError = ref('')
const scanCandidates = ref([])

async function scanIcons() {
    if (!props.project) return
    scanBusy.value = true
    scanError.value = ''
    scanCandidates.value = []
    let response
    try {
        response = await apiFetch(`/api/projects/${props.project.id}/icon/`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ action: 'scan' }),
        })
    } catch (error) {
        scanError.value = 'Network error. Please try again.'
        scanBusy.value = false
        return
    }
    if (!response.ok) {
        const data = await response.json().catch(() => ({}))
        scanError.value = data.error || 'Scan failed.'
        scanBusy.value = false
        return
    }
    const data = await response.json()
    scanCandidates.value = data.candidates || []
    scanDone.value = true
    scanBusy.value = false
}

function pickScannedIcon(candidate) {
    // Stage the chosen candidate (its preview is already the normalized image).
    pendingIcon.value = { type: 'set', image: candidate.image }
    iconError.value = ''
    scanCandidates.value = []
    scanDone.value = false
}

// Clear the icon/scan transient state when the dialog switches project (the
// main reset watcher above is immediate and runs before these refs exist).
watch(() => props.project, () => {
    pendingIcon.value = null
    scanDone.value = false
    scanCandidates.value = []
    scanError.value = ''
    iconError.value = ''
})

/**
 * Submit the create-project request, optionally asking the backend to create the directory.
 */
async function submitCreate({ createDirectory = false } = {}) {
    const trimmedDirectory = localDirectory.value.trim()
    const trimmedName = localName.value.trim()

    isSaving.value = true
    errorMessage.value = ''
    if (!createDirectory) {
        directoryNotFound.value = false
    }

    const body = {
        directory: trimmedDirectory,
        name: trimmedName || null,
        color: localColor.value || null,
    }
    if (selectedWorkspaceIds.value.length > 0) {
        // Backend adds these under _workspaces_lock alongside the pattern-based
        // auto-add, so the two never race / clobber each other.
        body.workspace_ids = [...selectedWorkspaceIds.value]
    }
    if (createDirectory) {
        body.create_directory = true
    }

    let response
    try {
        response = await apiFetch('/api/projects/', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(body),
        })
    } catch (error) {
        errorMessage.value = 'Network error. Please try again.'
        directoryNotFound.value = false
        isSaving.value = false
        return
    }

    if (!response.ok) {
        const data = await response.json()
        if (data.code === 'directory_not_found') {
            directoryNotFound.value = true
            errorMessage.value = ''
        } else {
            directoryNotFound.value = false
            errorMessage.value = data.error || 'Failed to create project'
        }
        isSaving.value = false
        return
    }

    const createdProject = await response.json()
    store.addProject(createdProject)
    emit('saved', createdProject)
    directoryNotFound.value = false
    isSaving.value = false
    close()
}

/**
 * Persist a trust / propagation change via the dedicated endpoint (edit mode).
 * Kept separate from the name/color PUT; the project_updated broadcast syncs the
 * store. ``trusted`` is true / false / null (null = reset to inherit).
 */
async function saveTrustIfChanged() {
    if (!props.project) return
    const currentChoice = trustChoiceFromValue(props.project.trust)
    const choiceChanged = localTrustChoice.value !== currentChoice
    const propChanged = localTrustChoice.value !== 'inherit'
        && localTrustPropagation.value !== (props.project.trust_propagation ?? false)
    if (!choiceChanged && !propChanged) return

    const trusted = localTrustChoice.value === 'trusted' ? true
        : localTrustChoice.value === 'untrusted' ? false
        : null
    const body = { trusted }
    if (trusted !== null) body.propagation = localTrustPropagation.value
    try {
        await apiFetch(`/api/projects/${props.project.id}/trust/decide/`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(body),
        })
    } catch (err) {
        // Non-fatal: name/color already saved; trust will reconcile on next action.
        console.warn('Failed to save project trust', err)
    }
}

/**
 * Save the project (create or update).
 */
async function handleSave() {
    if (isSaving.value) return

    // Trim whitespace from name
    const trimmedName = localName.value.trim()

    // Validate name length
    if (trimmedName.length > 25) {
        errorMessage.value = 'Name must be 25 characters or less'
        return
    }

    if (isCreateMode.value) {
        // --- Create mode ---
        const trimmedDirectory = localDirectory.value.trim()
        if (!trimmedDirectory) {
            errorMessage.value = 'Directory path is required'
            return
        }

        // Check name uniqueness client-side (same logic as edit mode)
        const isDuplicate = store.getProjects.some(p => p.name && p.name === trimmedName)
        if (trimmedName && isDuplicate) {
            errorMessage.value = 'A project with this name already exists'
            return
        }

        await submitCreate()
    } else {
        // --- Edit mode ---
        if (!props.project) return

        // Check uniqueness among other projects
        const otherProjects = store.getProjects.filter(p => p.id !== props.project.id)
        const isDuplicate = otherProjects.some(p => p.name && p.name === trimmedName)
        if (trimmedName && isDuplicate) {
            errorMessage.value = 'A project with this name already exists'
            return
        }

        isSaving.value = true
        errorMessage.value = ''

        const body = {
            name: trimmedName || null,
            color: localColor.value || null,
            archived: localArchived.value,
            // Per-project agent defaults: only the keys the user actually changed
            // (the PUT handler updates a field only when it is present).
            ...(agentDefaultsRef.value?.getChangedFields?.() || {}),
        }
        // Worktree directory is editable only for git main repos; for worktree
        // projects the section is hidden, so skip the field entirely rather than
        // sending null and silently wiping a value the user never saw.
        if (showWorktreeDirectory.value) {
            body.worktree_directory = localWorktreeDirectory.value.trim() || null
        }
        // Per-project default layout: send only when changed; '__inherit__' → null (inherit).
        const originalLayoutId = props.project.default_layout_id || LAYOUT_INHERIT
        if (localDefaultLayoutId.value !== originalLayoutId) {
            body.default_layout_id = localDefaultLayoutId.value === LAYOUT_INHERIT
                ? null
                : localDefaultLayoutId.value
        }
        // Per-project saved Browser-pane URLs: send only when changed.
        if (browserUrlsEditorRef.value) {
            const { entries, error } = browserUrlsEditorRef.value.getEntries()
            if (error) {
                errorMessage.value = error
                isSaving.value = false
                return
            }
            if (JSON.stringify(entries) !== JSON.stringify(props.project.browser_urls || [])) {
                body.browser_urls = entries
            }
        }
        let response
        try {
            response = await apiFetch(`/api/projects/${props.project.id}/`, {
                method: 'PUT',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify(body),
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

        // Apply workspace membership as targeted add/remove deltas, so only
        // what the user actually changed is touched.
        const initial = new Set(initialWorkspaceIds)
        const selected = new Set(selectedWorkspaceIds.value)
        const toAdd = [...selected].filter(id => !initial.has(id))
        const toRemove = [...initial].filter(id => !selected.has(id))
        if (toAdd.length || toRemove.length) {
            workspacesStore.applyProjectMembershipDeltas(props.project.id, toAdd, toRemove)
        }

        await saveTrustIfChanged()

        // Commit the staged icon change (if any). On failure, keep the dialog
        // open so the user sees the error and can retry.
        const iconOk = await commitPendingIcon()
        if (!iconOk) {
            isSaving.value = false
            return
        }

        emit('saved', updatedProject)
        isSaving.value = false
        close()
    }
}

// Expose methods for parent components
defineExpose({
    open,
    close,
})
</script>

<template>
    <wa-dialog ref="dialogRef" :label="isCreateMode ? 'New Project' : 'Edit Project'" class="project-edit-dialog" @wa-show="handleDialogShow" @wa-after-show="handleDialogAfterShow">
        <!-- Custom header: title + a live badge preview (edit mode), then the
             project path small underneath. The badge reflects the unsaved
             name/color being edited. -->
        <div slot="label" class="dialog-title">
            <div class="dialog-title-main">
                <span class="dialog-title-text">{{ isCreateMode ? 'New Project' : 'Edit Project' }}</span>
                <ProjectBadge
                    v-if="!isCreateMode && project"
                    :project-id="project.id"
                    :label="previewName"
                    :color-override="localColor"
                    class="dialog-title-badge"
                />
            </div>
            <ProjectDirectoryPath v-if="!isCreateMode && project?.directory" :project-id="project.id" class="dialog-title-path" />
            <!-- The stored "directory is gone" state, plus the manual re-check that
                 heals it once the folder is back (nothing detects that live). -->
            <div v-if="directoryMissing" class="dialog-title-missing">
                <span class="dialog-title-missing-text">
                    {{ recheckOutcome === 'confirmed' ? 'Still not found on disk' : 'Directory not found on disk' }}
                    <template v-if="recheckOutcome === 'error'"> — re-check failed</template>
                </span>
                <wa-button
                    size="small"
                    appearance="outlined"
                    :loading="recheckBusy"
                    :disabled="recheckBusy"
                    class="dialog-title-recheck"
                    @click="recheckDirectory"
                >
                    <wa-icon slot="start" name="arrow-rotate-right"></wa-icon>
                    Re-check
                </wa-button>
            </div>
        </div>
        <form :id="formId" class="dialog-content" @submit.prevent="handleSave">
            <!-- Two main tabs: "Project" holds everything; "Agent settings" the
                 per-provider defaults. In create mode there is no agent tab, and
                 the nav bar is hidden (single tab → looks like a flat form). -->
            <TabBar
                class="project-main-tabs"
                :class="{ 'hide-nav': isCreateMode }"
                :active="mainTab"
                @wa-tab-show="mainTab = $event.detail?.name ?? mainTab"
            >
                <wa-tab slot="nav" panel="general">Project</wa-tab>
                <wa-tab v-if="!isCreateMode" slot="nav" panel="agent">Agent settings</wa-tab>

                <wa-tab-panel name="general">
                    <div class="tab-body">
                        <!-- Create mode: directory input -->
                        <div v-if="isCreateMode" class="form-group">
                            <label class="form-label">Directory</label>
                            <div class="directory-input-row">
                                <wa-input
                                    ref="directoryInputRef"
                                    :value.prop="localDirectory"
                                    @input="onDirectoryInput"
                                    placeholder="/path/to/your/project"
                                    class="directory-input"
                                ></wa-input>
                                <DirectoryPickerPopup v-model="localDirectory" />
                            </div>
                            <div class="form-hint">Absolute path to the project directory</div>
                        </div>

                        <!-- (Edit mode shows the path under the dialog title, not here.) -->

                        <!-- Editable fields: name + color share one row -->
                        <div class="name-color-row">
                            <div class="form-group name-group">
                                <label class="form-label">Name</label>
                                <wa-input
                                    ref="nameInputRef"
                                    :value.prop="localName"
                                    @input="onNameInput"
                                    placeholder="Project name"
                                    maxlength="25"
                                ></wa-input>
                                <div class="form-hint">
                                    Optional display name (max 25 characters)
                                    — Named projects will always be displayed above unnamed ones.
                                    <template v-if="!isCreateMode && localName.trim()">
                                        — <a href="#" class="clear-name-link" @click.prevent="localName = ''">Remove name</a>
                                    </template>
                                </div>
                            </div>

                            <div class="form-group color-group">
                                <label class="form-label">Color</label>
                                <wa-color-picker
                                    ref="colorPickerRef"
                                    :value.prop="localColor"
                                    @change="onColorChange"
                                ></wa-color-picker>
                            </div>
                        </div>

                        <!-- Project icon (edit mode) — optional; falls back to the color dot -->
                        <div v-if="!isCreateMode" class="form-group icon-group">
                            <label class="form-label">Icon <span class="form-label-optional">(optional)</span></label>
                            <div class="icon-editor-row">
                                <ProjectMark
                                    class="icon-editor-preview"
                                    :icon-url="iconPreviewUrl"
                                    :color="localColor || props.project?.color || null"
                                />
                                <input
                                    ref="iconFileInputRef"
                                    type="file"
                                    accept="image/png,image/jpeg,image/webp,image/gif,image/svg+xml"
                                    class="icon-file-input"
                                    @change="onIconFileChange"
                                />
                                <wa-button size="small" type="button" @click="pickIconFile">
                                    <wa-icon slot="start" name="image"></wa-icon> Set icon…
                                </wa-button>
                            </div>
                            <div class="icon-editor-links" :class="{ 'is-busy': scanBusy }">
                                <a v-if="repoAnchorAvailable" href="#" class="icon-link" @click.prevent="scanIcons">Scan repository…</a>
                                <a v-if="iconIsOverridden" href="#" class="icon-link" @click.prevent="stageIcon('inherit')">Follow inherited icon</a>
                                <a v-if="!iconShowsColor" href="#" class="icon-link" @click.prevent="stageIcon('none')">Use color instead</a>
                                <wa-icon
                                    :id="iconHelpId"
                                    name="circle-question"
                                    class="icon-help-trigger"
                                    tabindex="0"
                                    role="button"
                                    label="About project icons"
                                ></wa-icon>
                                <AppTooltip :for="iconHelpId" trigger="click" force>
                                    A favicon or logo found in the git repository is used automatically. Set your own
                                    icon for this project — it applies to its sub-projects too, unless they set their
                                    own. “Scan repository…” lists every icon found so you can pick one; “Follow
                                    inherited icon” drops this project's own icon and inherits again; “Use color
                                    instead” shows the color dot.
                                </AppTooltip>
                            </div>
                            <div v-if="scanDone" class="icon-scan-results">
                                <template v-if="scanCandidates.length">
                                    <div class="icon-scan-caption">Pick one for this project:</div>
                                    <div class="icon-scan-list">
                                        <button
                                            v-for="c in scanCandidates"
                                            :key="c.rel_path"
                                            type="button"
                                            class="icon-scan-item"
                                            @click="pickScannedIcon(c)"
                                        >
                                            <img class="icon-scan-thumb" :src="c.image" alt="" />
                                            <span class="icon-scan-path">{{ c.rel_path }}</span>
                                        </button>
                                    </div>
                                </template>
                                <div v-else class="icon-scan-empty">No icons found in this repository.</div>
                            </div>
                            <wa-callout v-if="scanError" variant="danger" size="small" class="icon-error">{{ scanError }}</wa-callout>
                            <wa-callout v-if="iconError" variant="danger" size="small" class="icon-error">{{ iconError }}</wa-callout>
                        </div>

                        <wa-divider></wa-divider>

                        <!-- Worktree directory (edit mode, git main repos only —
                             hidden for worktree projects, which inherit it) -->
                        <div v-if="showWorktreeDirectory" class="form-group">
                            <label class="form-label">Worktree directory <HelpIconButton help-key="worktrees" label="What's a worktree?" /></label>
                            <div class="directory-input-row">
                                <wa-input
                                    :value.prop="localWorktreeDirectory"
                                    @input="onWorktreeDirectoryInput"
                                    placeholder="/absolute/path/to/worktrees"
                                    class="directory-input"
                                ></wa-input>
                                <DirectoryPickerPopup v-model="localWorktreeDirectory" :fallback-path="project.git_root || ''" />
                            </div>
                            <div class="form-hint">
                                Absolute base directory where new git worktrees of this project are created.
                                Leave empty to use the global default.
                            </div>
                            <wa-button
                                v-if="composedGlobalWorktreeDir"
                                type="button"
                                class="use-global-worktree-btn"
                                variant="neutral"
                                size="small"
                                @click="useGlobalWorktreeDir"
                            >
                                <wa-icon slot="start" name="wand-magic-sparkles"></wa-icon>
                                Use {{ composedGlobalWorktreeDir }}
                            </wa-button>
                        </div>

                        <wa-divider v-if="showWorktreeDirectory"></wa-divider>

                        <!-- Trust (edit mode only) -->
                        <div v-if="!isCreateMode" class="form-group">
                            <label class="form-label">Trust</label>
                            <p class="trust-intro">
                                Is this a project you created or trust (your own code, a well-known
                                open-source project, or your team's work)? Claude Code and Codex will be
                                able to read, edit and run files here.
                            </p>
                            <wa-select
                                :value.prop="localTrustChoice"
                                @change="localTrustChoice = $event.target.value"
                                size="small"
                                class="trust-select"
                            >
                                <wa-option value="inherit" label="Inherit">
                                    Inherit
                                    <small>No own decision — inherits the trust of a parent project.</small>
                                </wa-option>
                                <wa-option value="trusted">Trusted</wa-option>
                                <wa-option value="untrusted">Untrusted</wa-option>
                            </wa-select>
                            <div v-if="localTrustChoice === 'inherit'" class="form-hint">
                                <template v-if="resolvedTrust?.state === true">
                                    Inherited: <strong>trusted</strong><template v-if="resolvedSourceName"> (from {{ resolvedSourceName }})</template>
                                </template>
                                <template v-else-if="resolvedTrust?.state === false">
                                    Inherited: <strong>untrusted</strong><template v-if="resolvedSourceName"> (from {{ resolvedSourceName }})</template>
                                </template>
                                <template v-else>
                                    Not resolved — you'll be asked when starting a session here.
                                </template>
                            </div>
                            <wa-switch
                                v-else
                                class="trust-propagate-switch"
                                :checked="localTrustPropagation"
                                @change="localTrustPropagation = $event.target.checked"
                                size="small"
                            >
                                Apply to sub-folders and worktrees
                                <span class="trust-propagate-hint">
                                    — descendants inherit this decision until you decide otherwise.
                                </span>
                            </wa-switch>
                        </div>

                        <wa-divider v-if="!isCreateMode"></wa-divider>

                        <!-- Default layout (edit mode) -->
                        <div v-if="!isCreateMode" class="form-group">
                            <label class="form-label">Default layout for new sessions</label>
                            <wa-select
                                :value.prop="localDefaultLayoutId"
                                @change="localDefaultLayoutId = $event.target.value"
                                size="small"
                            >
                                <wa-option :value="LAYOUT_INHERIT">Inherit</wa-option>
                                <wa-option
                                    v-for="l in selectableLayouts"
                                    :key="l.id"
                                    :value="l.id"
                                    :label="l.name"
                                >{{ l.name }}</wa-option>
                            </wa-select>
                            <div class="form-hint">
                                Layout new sessions in this project open with. Inherit = use the parent
                                project's default (for a worktree), then the global default.
                            </div>
                        </div>

                        <wa-divider v-if="!isCreateMode"></wa-divider>

                        <!-- Browser pane URLs (edit mode) -->
                        <div v-if="!isCreateMode" class="form-group">
                            <label class="form-label">Browser URLs</label>
                            <BrowserUrlListEditor
                                ref="browserUrlsEditorRef"
                                :entries="props.project?.browser_urls || []"
                            />
                            <div class="form-hint">
                                URLs saved for the session Browser tab; the selected one is the
                                Home default. Empty list = inherit — the parent project's URLs
                                (for a worktree), then a containing workspace's.
                            </div>
                        </div>

                        <wa-divider v-if="!isCreateMode"></wa-divider>

                        <!-- Workspaces -->
                        <div class="form-group">
                            <div class="form-label-row">
                                <label class="form-label">Workspaces</label>
                                <wa-switch
                                    v-if="hasArchivedWorkspaces"
                                    :checked="localShowArchivedWorkspaces"
                                    @change="localShowArchivedWorkspaces = $event.target.checked"
                                    size="small"
                                >
                                    Show archived
                                </wa-switch>
                            </div>

                            <div v-if="selectedWorkspaceEntries.length > 0" class="workspace-list">
                                <div
                                    v-for="(entry, visibleIndex) in selectedWorkspaceEntries"
                                    :key="entry.id"
                                    class="workspace-row"
                                >
                                    <span class="workspace-row-name">
                                        <wa-icon name="layer-group" auto-width :style="workspaceColor(entry.id) ? { color: workspaceColor(entry.id) } : null"></wa-icon>
                                        {{ workspaceName(entry.id) }}
                                    </span>
                                    <button
                                        type="button"
                                        class="action-btn action-btn-danger"
                                        @click="removeWorkspace(visibleIndex)"
                                        title="Remove from workspace"
                                    >
                                        <wa-icon name="xmark" />
                                    </button>
                                </div>
                            </div>

                            <div v-else-if="selectedWorkspaceIds.length > 0" class="empty-workspaces-message">
                                All workspaces for this project are archived (hidden).
                            </div>

                            <div v-else class="empty-workspaces-message">
                                {{ isCreateMode ? 'No workspaces selected.' : 'Not in any workspace.' }}
                            </div>

                            <!-- Add to a workspace -->
                            <wa-select
                                v-if="availableWorkspaces.length > 0"
                                value=""
                                @change="addWorkspace"
                                placeholder="Add to a workspace..."
                                size="small"
                                class="add-workspace-select"
                            >
                                <wa-option
                                    v-for="ws in availableWorkspaces"
                                    :key="ws.id"
                                    :value="ws.id"
                                    :label="ws.name"
                                >
                                    <wa-icon name="layer-group" auto-width :style="ws.color ? { color: ws.color } : null"></wa-icon>
                                    {{ ws.name }}
                                </wa-option>
                            </wa-select>

                            <!-- Create mode: add workspaces whose auto-add pattern matches -->
                            <template v-if="isCreateMode">
                                <div class="scan-row">
                                    <wa-button type="button" variant="neutral" size="small" @click="scanMatchingWorkspaces">
                                        <wa-icon name="magnifying-glass-plus" slot="start"></wa-icon>
                                        Add matching workspaces
                                    </wa-button>
                                    <span v-if="workspaceScanFeedback" class="scan-feedback">{{ workspaceScanFeedback }}</span>
                                </div>
                                <p class="form-hint">
                                    Workspaces whose auto-add pattern matches this directory will be added
                                    automatically on creation, even if you remove them here.
                                </p>
                            </template>
                        </div>
                    </div>
                </wa-tab-panel>

                <wa-tab-panel v-if="!isCreateMode" name="agent">
                    <div class="tab-body">
                        <ProjectAgentDefaultsSection ref="agentDefaultsRef" :project="project" />
                    </div>
                </wa-tab-panel>
            </TabBar>

            <!-- Callouts live outside the tabs so they stay visible on either tab -->
            <wa-callout v-if="directoryNotFound" variant="warning" size="small" class="directory-not-found-callout">
                <div class="directory-not-found-content">
                    <span>This directory does not exist. Do you want to create it?</span>
                    <div class="directory-not-found-actions">
                        <wa-button size="small" variant="brand" :disabled="isSaving" @click="submitCreate({ createDirectory: true })">
                            Create directory
                        </wa-button>
                        <wa-button size="small" variant="neutral" appearance="outlined" @click="directoryNotFound = false">
                            Cancel
                        </wa-button>
                    </div>
                </div>
            </wa-callout>

            <!-- Error message -->
            <wa-callout v-if="errorMessage" variant="danger" size="small">
                {{ errorMessage }}
            </wa-callout>
        </form>

        <!-- Footer buttons -->
        <div slot="footer" class="dialog-footer">
            <wa-switch
                v-if="!isCreateMode"
                :checked="localArchived"
                @change="localArchived = $event.target.checked"
                size="small"
                class="footer-archived-switch"
            >
                Archived
            </wa-switch>
            <wa-button variant="neutral" appearance="outlined" @click="close" :disabled="isSaving">
                Cancel
            </wa-button>
            <wa-button ref="saveButtonRef" type="submit" variant="brand" :disabled="isSaving">
                {{ isCreateMode ? 'Create' : 'Save' }}
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

/* Body of each main tab panel: same vertical rhythm as the old flat form. */
.tab-body {
    display: flex;
    flex-direction: column;
    gap: var(--wa-space-m);
}

/* Create mode has a single tab — hide the nav bar so it reads as a flat form. */
.project-main-tabs.hide-nav::part(nav) {
    display: none;
}

/* Dialog header: title with the project path as a small line underneath. */
.dialog-title {
    display: flex;
    flex-direction: column;
    gap: var(--wa-space-3xs);
    min-width: 0;
}

/* Title row: "Edit Project" on the left, the live badge preview on the right.
   The gap is the minimum separation kept when a long title leaves no free
   space (otherwise margin-left:auto pushes the badge to the right edge). */
.dialog-title-main {
    display: flex;
    align-items: center;
    gap: var(--wa-space-m);
    flex-wrap: wrap;
    min-width: 0;
}

/* Live badge preview pinned to the right of the title — no extra styling, just
   the badge a touch smaller. min/max-width keep names ellipsized. */
.dialog-title-badge {
    margin-left: auto;
    font-size: 0.85em;
    min-width: 0;
    max-width: 100%;
}

.dialog-title-path {
    font-size: var(--wa-font-size-xs);
    font-weight: var(--wa-font-weight-normal);
    color: var(--wa-color-text-quiet);
    word-break: break-all;
}

.dialog-title-missing {
    display: flex;
    align-items: center;
    gap: var(--wa-space-s);
    flex-wrap: wrap;
    font-size: var(--wa-font-size-xs);
    font-weight: var(--wa-font-weight-normal);
}

.dialog-title-missing-text {
    color: var(--wa-color-warning-on-quiet);
}

.dialog-title-recheck {
    font-size: var(--wa-font-size-xs);
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

.form-hint {
    font-size: var(--wa-font-size-xs);
    color: var(--wa-color-text-quiet);
}

.form-label-optional {
    font-weight: var(--wa-font-weight-normal);
    color: var(--wa-color-text-quiet);
}

.icon-editor-row {
    display: flex;
    align-items: center;
    gap: var(--wa-space-m);
}

.icon-editor-preview {
    --project-mark-size: 2.5rem;
    flex: 0 0 auto;
}

.icon-file-input {
    display: none;
}

/* Secondary actions rendered as text links on their own line. */
.icon-editor-links {
    display: flex;
    flex-wrap: wrap;
    gap: var(--wa-space-m);
    margin-top: var(--wa-space-xs);
}

.icon-editor-links.is-busy {
    pointer-events: none;
    opacity: 0.5;
}

.icon-link {
    font-size: var(--wa-font-size-s);
    color: var(--wa-color-brand-text);
    text-decoration: none;
    cursor: pointer;
}

.icon-link:hover {
    text-decoration: underline;
}

.icon-help-trigger {
    color: var(--wa-color-text-quiet);
    cursor: pointer;
    font-size: var(--wa-font-size-m);
    align-self: center;
}

.icon-help-trigger:hover {
    color: var(--wa-color-text-normal);
}

.icon-error {
    margin-top: var(--wa-space-xs);
}

.icon-scan-results {
    margin-top: var(--wa-space-s);
}

.icon-scan-caption {
    font-size: var(--wa-font-size-xs);
    color: var(--wa-color-text-quiet);
    margin-bottom: var(--wa-space-xs);
}

.icon-scan-empty {
    font-size: var(--wa-font-size-s);
    color: var(--wa-color-text-quiet);
}

/* Vertical list — each row shows the thumbnail and the FULL relative path. */
.icon-scan-list {
    display: flex;
    flex-direction: column;
    gap: var(--wa-space-2xs);
}

.icon-scan-item {
    /* Reset WebAwesome's native.css form-control sizing on a bare <button>.
       Rendered like the link actions: no background/border, left-aligned. */
    appearance: none;
    -webkit-appearance: none;
    height: auto;
    min-height: 0;
    line-height: normal;
    font: inherit;
    display: flex;
    flex-direction: row;
    align-items: center;
    justify-content: flex-start;
    gap: var(--wa-space-s);
    width: 100%;
    padding: var(--wa-space-2xs) var(--wa-space-s);  /* left padding = the requested indent */
    border: none;
    background: none;
    cursor: pointer;
    text-align: left;
    /* Set on the button so it wins over the native <button> default text color. */
    color: var(--wa-color-brand-text);
}

.icon-scan-item:hover:not(:disabled) .icon-scan-path {
    text-decoration: underline;
}

.icon-scan-item:disabled {
    opacity: 0.5;
    cursor: default;
}

.icon-scan-thumb {
    flex: 0 0 auto;
    width: 1.75rem;
    height: 1.75rem;
    object-fit: contain;
}

.icon-scan-path {
    font-size: var(--wa-font-size-s);
    color: inherit;
    word-break: break-all;
    min-width: 0;
}

.trust-intro {
    margin: 0;
    font-size: var(--wa-font-size-s);
    color: var(--wa-color-text-quiet);
}

/* Per-option helptext inside the trust select dropdown (e.g. "Inherit"). The
   explicit `label` on the option keeps the collapsed display clean; this only
   styles the dropdown description line. */
.trust-select small {
    display: block;
    margin-top: var(--wa-space-3xs);
    font-size: var(--wa-font-size-xs);
    opacity: 0.7;
    white-space: normal;
}

.trust-propagate-switch {
    font-size: var(--wa-font-size-s);
}

.trust-propagate-hint {
    color: var(--wa-color-text-quiet);
}

.directory-input-row {
    display: flex;
    align-items: center;
    gap: var(--wa-space-2xs);
}

.directory-input {
    flex: 1;
    min-width: 0;
}

/* "Use the global default" shortcut under the worktree directory. */
.use-global-worktree-btn {
    margin-top: var(--wa-space-2xs);
    align-self: flex-start;
    max-width: 100%;
}

.use-global-worktree-btn::part(label) {
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
}

/* -- Workspaces ------------------------------------------------------------- */
.form-label-row {
    display: flex;
    align-items: center;
    justify-content: space-between;
    gap: var(--wa-space-s);
}

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
    padding: var(--wa-space-3xs) var(--wa-space-xs);
}

.workspace-row-name {
    flex: 1;
    min-width: 0;
    display: flex;
    align-items: center;
    gap: var(--wa-space-2xs);
    font-size: var(--wa-font-size-s);
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
}

.empty-workspaces-message {
    font-size: var(--wa-font-size-s);
    color: var(--wa-color-text-quiet);
    padding: var(--wa-space-xs) 0;
}

.add-workspace-select {
    max-width: 280px;
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
    background: var(--wa-color-surface-base);
    color: var(--wa-color-text-base);
}

.action-btn-danger:hover {
    color: var(--wa-color-danger-60);
}

.clear-name-link {
    color: var(--wa-color-brand-text);
    text-decoration: none;
}

.clear-name-link:hover {
    text-decoration: underline;
}

.directory-not-found-content {
    display: flex;
    flex-direction: column;
    gap: var(--wa-space-s);
}

.directory-not-found-actions {
    display: flex;
    gap: var(--wa-space-xs);
}

.dialog-footer {
    display: flex;
    gap: var(--wa-space-s);
    justify-content: flex-end;
    width: 100%;
    align-items: center;
}

.footer-archived-switch {
    margin-right: auto;
}
</style>
