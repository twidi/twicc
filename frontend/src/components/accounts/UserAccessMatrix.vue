<script setup>
// UserAccessMatrix.vue — what one member may reach, workspace by workspace.
//
// Each workspace row sets a level for the whole workspace (covering projects
// added to it later); expanding it lists its projects with their own direct
// level. A level inherited from a workspace shows greyed with a badge, and the
// effective level is the stronger of the two. Changes are buffered until Save
// (PUT /api/admin/users/<id>/grants/ replaces every grant at once).
import { computed, ref, watch } from 'vue'
import { useI18n } from 'vue-i18n'
import { localUiMessage, resolveUiMessage } from '../../i18n/uiMessages.js'
import { useDataStore } from '../../stores/data'
import { useWorkspacesStore } from '../../stores/workspaces'
import { saveUserGrants } from '../../utils/accountsApi'
import { ACCESS_LEVELS, buildAccessMatrix, grantsPayload } from '../../utils/accountsDisplay'

const props = defineProps({
    user: { type: Object, required: true },
})

const emit = defineEmits(['saved'])

const { t } = useI18n()
const dataStore = useDataStore()
const workspacesStore = useWorkspacesStore()

const projectLevels = ref({})
const workspaceLevels = ref({})
const expanded = ref(new Set())
const saving = ref(false)
const error = ref('')
const notice = ref('')
const dirty = ref(false)

function reset() {
    projectLevels.value = Object.fromEntries((props.user.project_grants || []).map(g => [g.project_id, g.level]))
    workspaceLevels.value = Object.fromEntries((props.user.workspace_grants || []).map(g => [g.workspace_id, g.level]))
    dirty.value = false
    error.value = ''
    notice.value = ''
}

watch(() => props.user, reset, { immediate: true })

const levelOptions = computed(() => [
    { value: 'none', label: t('accounts.levels.none') },
    ...ACCESS_LEVELS.map(level => ({ value: level, label: t(`accounts.levels.${level}`) })),
])

const matrix = computed(() => buildAccessMatrix({
    workspaces: workspacesStore.getAllWorkspaces.filter(ws => !ws.archived || workspaceLevels.value[ws.id]),
    projects: dataStore.getProjects,
    projectLevels: projectLevels.value,
    workspaceLevels: workspaceLevels.value,
}))

function groupKey(group) {
    return group.workspace ? group.workspace.id : '__none__'
}

function toggle(group) {
    const key = groupKey(group)
    const next = new Set(expanded.value)
    if (next.has(key)) next.delete(key)
    else next.add(key)
    expanded.value = next
}

function setWorkspaceLevel(workspaceId, value) {
    workspaceLevels.value = { ...workspaceLevels.value, [workspaceId]: value === 'none' ? '' : value }
    dirty.value = true
    notice.value = ''
}

function setProjectLevel(projectId, value) {
    projectLevels.value = { ...projectLevels.value, [projectId]: value === 'none' ? '' : value }
    dirty.value = true
    notice.value = ''
}

function projectName(project) {
    return dataStore.getProjectDisplayName(project.id)
}

function grantedCount(group) {
    return group.rows.filter(row => row.effective).length
}

async function save() {
    saving.value = true
    error.value = ''
    const result = await saveUserGrants(props.user.id, grantsPayload(projectLevels.value, workspaceLevels.value))
    saving.value = false
    if (!result.ok) {
        error.value = result.error
        return
    }
    dirty.value = false
    notice.value = localUiMessage('accounts.matrix.saved')
    emit('saved', result.data.user)
}
</script>

<template>
    <div class="matrix">
        <p class="matrix-intro">{{ $t('accounts.matrix.intro') }}</p>

        <div v-if="!matrix.groups.length" class="matrix-empty">{{ $t('accounts.matrix.noProjects') }}</div>

        <div v-for="group in matrix.groups" :key="groupKey(group)" class="matrix-group">
            <div class="matrix-group-head">
                <wa-button
                    size="small"
                    appearance="plain"
                    class="matrix-toggle"
                    :aria-expanded="expanded.has(groupKey(group)) ? 'true' : 'false'"
                    @click="toggle(group)"
                >
                    <wa-icon :name="expanded.has(groupKey(group)) ? 'chevron-down' : 'chevron-right'"></wa-icon>
                </wa-button>
                <span class="matrix-group-name" @click="toggle(group)">
                    <wa-icon :name="group.workspace ? 'layer-group' : 'folder-open'" class="matrix-group-icon"></wa-icon>
                    {{ group.workspace ? group.workspace.name : $t('accounts.matrix.noWorkspace') }}
                    <span class="matrix-count">{{ $t('accounts.matrix.grantedCount', { granted: grantedCount(group), total: group.rows.length }) }}</span>
                </span>
                <wa-select
                    v-if="group.workspace"
                    size="small"
                    class="matrix-level"
                    :value.prop="group.level || 'none'"
                    :aria-label="$t('accounts.matrix.workspaceLevel', { workspace: group.workspace.name })"
                    @change.stop="setWorkspaceLevel(group.workspace.id, $event.target.value)"
                    @input.stop
                    @wa-show.stop
                    @wa-hide.stop
                    @wa-after-show.stop
                    @wa-after-hide.stop
                >
                    <wa-option v-for="option in levelOptions" :key="option.value" :value="option.value">{{ option.label }}</wa-option>
                </wa-select>
            </div>

            <ul v-if="expanded.has(groupKey(group))" class="matrix-rows">
                <li v-for="row in group.rows" :key="row.project.id" class="matrix-row" :class="{ 'matrix-row--none': !row.effective }">
                    <span class="matrix-project">{{ projectName(row.project) }}</span>
                    <wa-tag
                        v-if="row.inherited"
                        size="small"
                        variant="neutral"
                        class="matrix-inherited"
                    >
                        {{ $t('accounts.matrix.inherited', { level: $t(`accounts.levels.${row.inherited}`) }) }}
                    </wa-tag>
                    <wa-select
                        size="small"
                        class="matrix-level"
                        :value.prop="row.direct || 'none'"
                        :aria-label="$t('accounts.matrix.projectLevel', { project: projectName(row.project) })"
                        @change.stop="setProjectLevel(row.project.id, $event.target.value)"
                        @input.stop
                        @wa-show.stop
                        @wa-hide.stop
                        @wa-after-show.stop
                        @wa-after-hide.stop
                    >
                        <wa-option v-for="option in levelOptions" :key="option.value" :value="option.value">{{ option.label }}</wa-option>
                    </wa-select>
                </li>
                <li v-if="!group.rows.length" class="matrix-empty">{{ $t('accounts.matrix.emptyWorkspace') }}</li>
            </ul>
        </div>

        <p class="matrix-hint">
            <wa-icon name="code-branch"></wa-icon>
            {{ $t('accounts.matrix.worktreesHint') }}
        </p>

        <wa-callout v-if="error" variant="danger" size="small">
            <wa-icon slot="icon" name="circle-exclamation"></wa-icon>
            {{ resolveUiMessage(error) }}
        </wa-callout>
        <wa-callout v-if="notice" variant="success" size="small">
            <wa-icon slot="icon" name="circle-check"></wa-icon>
            {{ resolveUiMessage(notice) }}
        </wa-callout>

        <div class="matrix-actions">
            <wa-button size="small" appearance="outlined" :disabled="!dirty || saving" @click="reset">
                {{ $t('accounts.matrix.discard') }}
            </wa-button>
            <wa-button size="small" variant="brand" :loading="saving" :disabled="!dirty || saving" @click="save">
                <wa-icon slot="start" name="floppy-disk"></wa-icon>
                {{ $t('accounts.matrix.save') }}
            </wa-button>
        </div>
    </div>
</template>

<style scoped>
.matrix {
    display: flex;
    flex-direction: column;
    gap: var(--wa-space-s);
}

.matrix-intro,
.matrix-hint {
    margin: 0;
    font-size: var(--wa-font-size-s);
    color: var(--wa-color-text-quiet);
}

.matrix-hint {
    display: flex;
    align-items: center;
    gap: var(--wa-space-2xs);
}

.matrix-group {
    border: var(--wa-border-width-s) solid var(--wa-color-surface-border);
    border-radius: var(--wa-border-radius-m);
    padding: var(--wa-space-2xs) var(--wa-space-s);
}

.matrix-group-head {
    display: flex;
    align-items: center;
    gap: var(--wa-space-xs);
}

.matrix-group-name {
    flex: 1;
    min-width: 0;
    display: flex;
    align-items: center;
    gap: var(--wa-space-2xs);
    font-weight: var(--wa-font-weight-semibold);
    cursor: pointer;
}

.matrix-group-icon {
    color: var(--wa-color-text-quiet);
}

.matrix-count {
    font-weight: normal;
    font-size: var(--wa-font-size-xs);
    color: var(--wa-color-text-quiet);
}

.matrix-rows {
    list-style: none;
    margin: var(--wa-space-2xs) 0 0;
    padding: 0 0 0 var(--wa-space-xl);
    display: flex;
    flex-direction: column;
    gap: var(--wa-space-2xs);
}

.matrix-row {
    display: flex;
    align-items: center;
    gap: var(--wa-space-xs);
}

.matrix-row--none .matrix-project {
    color: var(--wa-color-text-quiet);
}

.matrix-project {
    flex: 1;
    min-width: 0;
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
}

.matrix-inherited {
    opacity: 0.75;
}

.matrix-level {
    flex: none;
    width: 9rem;
}

.matrix-empty {
    font-size: var(--wa-font-size-s);
    color: var(--wa-color-text-quiet);
}

.matrix-actions {
    display: flex;
    justify-content: flex-end;
    gap: var(--wa-space-s);
}
</style>
