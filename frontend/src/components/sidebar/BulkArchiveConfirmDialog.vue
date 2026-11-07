<script setup>
import { localUiMessage, resolveUiMessage } from '../../i18n/uiMessages.js'
import { useI18n } from 'vue-i18n'

const { t } = useI18n()

import { computed, nextTick, onUnmounted, ref, watch } from 'vue'
import { useDataStore } from '../../stores/data'
import { useWorkspacesStore } from '../../stores/workspaces'
import { DURATION_PRESETS, presetToDate } from '../../utils/datePresets'
import ProjectBadge from '../project/ProjectBadge.vue'

const props = defineProps({
    open: { type: Boolean, required: true },
    preset: { type: String, required: true },        // e.g. '7d'
    scope: { type: Object, required: true },         // { type, id }
    titleQuery: { type: String, default: '' },       // sidebar filter snapshot
    includeArchivedProjects: { type: Boolean, default: false },  // global toggle snapshot
})

const emit = defineEmits(['update:open', 'archived'])

const dataStore = useDataStore()
const workspacesStore = useWorkspacesStore()

const currentPreset = ref(props.preset)
watch(() => props.preset, (v) => { currentPreset.value = v })

// Snapshot of the sidebar filter at dialog-open time. The user can clear it
// from the dialog (chip remove) to broaden the archive scope without closing.
const currentTitleQuery = ref(props.titleQuery)

// Snapshot of the global "Show archived projects" toggle. The user can flip
// this from within the dialog via a wa-switch — but only when the backend
// confirms there are eligible sessions in archived projects (see
// hasArchivedInScope below).
const currentIncludeArchivedProjects = ref(props.includeArchivedProjects)

// Populated from the dry-run response. Drives switch visibility — we only
// show the toggle when flipping it would actually change the count.
const hasArchivedInScope = ref(false)

const count = ref(null)         // null = loading, number = result
const error = ref(null)
const submitting = ref(false)

let abortController = null

const currentLabel = computed(
    () => DURATION_PRESETS.find((p) => p.value === currentPreset.value)?.label ?? currentPreset.value,
)

const workspace = computed(() => {
    if (props.scope.type !== 'workspace') return null
    return workspacesStore.workspaces.find((w) => w.id === props.scope.id) ?? null
})

// Mirror getVisibleProjectIds' shape but key off the dialog-local toggle
// rather than the global "show archived projects" setting, so the workspace
// projects list grows/shrinks in sync with the switch.
const workspaceProjectIds = computed(() => {
    if (!workspace.value) return []
    return (workspace.value.projectIds || []).filter((pid) => {
        const project = dataStore.getProject(pid)
        if (!project) return false
        return currentIncludeArchivedProjects.value || !project.archived
    })
})

async function refreshCount() {
    const iso = presetToDate(currentPreset.value)
    if (!iso) {
        // Defensive: shouldn't happen since wa-select only emits known presets,
        // but bail gracefully rather than send a malformed request.
        error.value = localUiMessage('session.bulkArchive.errors.unknownPreset', { preset: currentPreset.value })
        return
    }

    if (abortController) abortController.abort()
    abortController = new AbortController()

    count.value = null
    error.value = null

    try {
        const res = await dataStore.bulkArchiveSessions({
            olderThan: iso,
            scope: props.scope,
            titleQuery: currentTitleQuery.value,
            includeArchivedProjects: currentIncludeArchivedProjects.value,
            dryRun: true,
            signal: abortController.signal,
        })
        count.value = res.count
        hasArchivedInScope.value = res.has_archived_in_scope ?? false
    } catch (err) {
        if (err.name === 'AbortError') return
        error.value = err.uiMessage || err.message || localUiMessage('session.bulkArchive.errors.count')
    }
}

watch(() => props.open, (isOpen) => {
    if (!isOpen) return
    // Snapshot props into local refs. Track whether each changed so we know
    // if the field watchers will fire (and trigger refreshCount), or if we
    // need to call refreshCount ourselves. When more than one changed, the
    // last-firing watcher's request aborts the earlier ones via abortController
    // — final state is correct.
    const presetChanged = currentPreset.value !== props.preset
    const titleChanged = currentTitleQuery.value !== props.titleQuery
    const archivedChanged = currentIncludeArchivedProjects.value !== props.includeArchivedProjects
    currentPreset.value = props.preset
    currentTitleQuery.value = props.titleQuery
    currentIncludeArchivedProjects.value = props.includeArchivedProjects
    if (!presetChanged && !titleChanged && !archivedChanged) refreshCount()
})

watch(currentPreset, () => {
    if (props.open) refreshCount()
})

watch(currentTitleQuery, () => {
    if (props.open) refreshCount()
})

watch(currentIncludeArchivedProjects, () => {
    if (props.open) refreshCount()
})

onUnmounted(() => {
    if (abortController) abortController.abort()
})

async function handleConfirm() {
    if (count.value === 0 || count.value === null || submitting.value) return
    const iso = presetToDate(currentPreset.value)
    if (!iso) {
        error.value = localUiMessage('session.bulkArchive.errors.unknownPreset', { preset: currentPreset.value })
        return
    }
    submitting.value = true
    error.value = null
    try {
        const res = await dataStore.bulkArchiveSessions({
            olderThan: iso,
            scope: props.scope,
            titleQuery: currentTitleQuery.value,
            includeArchivedProjects: currentIncludeArchivedProjects.value,
        })
        emit('archived', { count: res.count })
        emit('update:open', false)
    } catch (err) {
        error.value = err.uiMessage || err.message || localUiMessage('session.bulkArchive.errors.archive')
    } finally {
        submitting.value = false
    }
}

function handleCancel() {
    emit('update:open', false)
}

// Wire the submit button to the form by id (Web Awesome wa-button does not
// expose `form` as a property — must be set via setAttribute on the host).
const submitButtonRef = ref(null)
const FORM_ID = 'bulk-archive-confirm-form'
watch(submitButtonRef, async (el) => {
    if (el) {
        await nextTick()
        el.setAttribute('form', FORM_ID)
    }
})

function handleDialogHide(event) {
    // wa-hide bubbles up from nested wa-select panels too — only react to the
    // dialog's own hide, otherwise changing the duration would close the dialog.
    if (event.target !== event.currentTarget) return
    if (submitting.value) {
        event.preventDefault()
        return
    }
    if (props.open) emit('update:open', false)
}
</script>

<template>
    <wa-dialog
        :open="open"
        :label="$t('session.bulkArchive.title')"
        @wa-hide="handleDialogHide"
        style="--width: min(520px, calc(100vw - 2rem));"
    >
        <form :id="FORM_ID" @submit.prevent="handleConfirm">
            <div class="bulk-archive-row">
                <span>{{ $t('session.bulkArchive.olderThan') }}</span>
                <wa-select v-model="currentPreset" size="small" class="duration-select">
                    <wa-option
                        v-for="p in DURATION_PRESETS"
                        :key="p.value"
                        :value="p.value"
                    >{{ p.label }}</wa-option>
                </wa-select>
            </div>

            <div class="bulk-archive-scope">
                <div class="bulk-archive-scope-main">
                    <span class="bulk-archive-scope-label">{{ $t('session.bulkArchive.scope') }}</span>
                    <ProjectBadge v-if="scope.type === 'project'" :project-id="scope.id" />
                    <template v-else-if="scope.type === 'workspace' && workspace">
                        <wa-icon
                            name="layer-group"
                            auto-width
                            :style="workspace.color ? { color: workspace.color } : null"
                        ></wa-icon>
                        <span>{{ workspace.name }}</span>
                    </template>
                    <span v-else>{{ $t('session.bulkArchive.allProjects') }}</span>
                </div>
                <div v-if="scope.type === 'workspace' && workspace" class="workspace-projects">
                    <span class="bulk-archive-scope-label">{{ $t('session.bulkArchive.workspaceProjects') }}</span>
                    <ProjectBadge
                        v-for="pid in workspaceProjectIds"
                        :key="pid"
                        :project-id="pid"
                    />
                </div>
                <div v-if="currentTitleQuery" class="bulk-archive-filter">
                    <span class="bulk-archive-scope-label">{{ $t('session.bulkArchive.matching') }}</span>
                    <wa-tag pill with-remove @wa-remove="currentTitleQuery = ''">
                        {{ currentTitleQuery }}
                    </wa-tag>
                </div>
                <div v-if="hasArchivedInScope" class="bulk-archive-archived-toggle">
                    <wa-switch
                        size="small"
                        :checked="currentIncludeArchivedProjects"
                        @change="currentIncludeArchivedProjects = $event.target.checked"
                    >
                        {{ $t('session.bulkArchive.includeArchived') }}
                    </wa-switch>
                </div>
            </div>

            <div class="bulk-archive-count">
                <template v-if="count === null && !error">
                    <wa-spinner></wa-spinner>
                    <span>{{ $t('session.bulkArchive.counting') }}</span>
                </template>
                <template v-else-if="count === 0">
                    {{ $t('session.bulkArchive.empty', { duration: currentLabel }) }}
                </template>
                <template v-else-if="count !== null">
                    <i18n-t keypath="session.bulkArchive.confirmCount" scope="global" :plural="count"><template #count><strong>{{ count }}</strong></template></i18n-t>
                </template>
            </div>

            <div class="bulk-archive-hint">
                {{ $t('session.bulkArchive.exclusions') }}
            </div>

            <wa-callout v-if="error" variant="danger">{{ resolveUiMessage(error) }}</wa-callout>
        </form>

        <wa-button slot="footer" appearance="plain" :disabled="submitting" @click="handleCancel">
            {{ $t('common.actions.cancel') }}
        </wa-button>
        <wa-button
            slot="footer"
            ref="submitButtonRef"
            type="submit"
            variant="brand"
            :disabled="count === null || count === 0 || submitting"
            :loading="submitting"
        >
            <template v-if="count && count > 0">{{ $t('session.bulkArchive.archiveCount', { count }) }}</template>
            <template v-else>{{ $t('common.actions.archive') }}</template>
        </wa-button>
    </wa-dialog>
</template>

<style scoped>
.bulk-archive-row {
    display: flex;
    align-items: center;
    gap: var(--wa-space-xs);
    margin-bottom: var(--wa-space-m);
    flex-wrap: wrap;
}

.duration-select {
    min-width: 8rem;
}

.bulk-archive-scope {
    margin-bottom: var(--wa-space-m);
}

.bulk-archive-scope-main {
    display: flex;
    align-items: center;
    gap: var(--wa-space-xs);
    flex-wrap: wrap;
}

.bulk-archive-scope-label {
    color: var(--wa-color-text-quiet);
    font-size: var(--wa-font-size-s);
}

.workspace-projects {
    display: flex;
    flex-wrap: wrap;
    align-items: center;
    gap: var(--wa-space-xs);
    margin-top: var(--wa-space-2xs);
    font-size: var(--wa-font-size-s);
}

.bulk-archive-filter {
    display: flex;
    flex-wrap: wrap;
    align-items: center;
    gap: var(--wa-space-xs);
    margin-top: var(--wa-space-2xs);
    font-size: var(--wa-font-size-s);
}

.bulk-archive-archived-toggle {
    margin-top: var(--wa-space-xs);
    font-size: var(--wa-font-size-s);
}

.bulk-archive-count {
    display: flex;
    align-items: center;
    gap: var(--wa-space-xs);
    margin-bottom: var(--wa-space-xs);
    min-height: 1.5rem;
}

.bulk-archive-hint {
    color: var(--wa-color-text-quiet);
    font-size: var(--wa-font-size-s);
}
</style>
