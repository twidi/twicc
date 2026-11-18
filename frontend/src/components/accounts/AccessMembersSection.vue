<script setup>
// AccessMembersSection.vue — "who can reach this project/workspace" (admins only).
//
// Self-contained: loads the members of one project or workspace and the account
// list, and saves each change at once (PUT …/access/<user_id>/). Used inside the
// project edit dialog and the workspace manage dialog, so every wa-select stops
// its own wa-show/wa-hide family: unguarded, they would bubble to the dialog and
// be taken for the dialog opening/closing (see CLAUDE.md "Bubbling custom events").
import { computed, onMounted, ref, watch } from 'vue'
import { useI18n } from 'vue-i18n'
import { resolveUiMessage } from '../../i18n/uiMessages.js'
import { getTargetAccess, listUsers, setTargetAccess } from '../../utils/accountsApi'
import { ACCESS_LEVELS, userInitials } from '../../utils/accountsDisplay'

const props = defineProps({
    // 'project' or 'workspace'
    kind: { type: String, required: true },
    targetId: { type: String, required: true },
})

const { t } = useI18n()

const members = ref([])
const users = ref([])
const loading = ref(false)
const busyUserId = ref(null)
const error = ref(null)
const newUserId = ref('')
const newLevel = ref('view')

const levelOptions = computed(() => [
    { value: 'none', label: t('accounts.levels.none') },
    ...ACCESS_LEVELS.map(level => ({ value: level, label: t(`accounts.levels.${level}`) })),
])

// The level a row's select shows: the direct grant (project) or the grant (workspace).
function rowLevel(row) {
    return (props.kind === 'project' ? row.direct_level : row.level) || 'none'
}

// Accounts that can still be added: active members not listed yet.
const addableUsers = computed(() => {
    const listed = new Set(members.value.map(row => row.user.id))
    return users.value.filter(user => user.role === 'member' && user.is_active && !listed.has(user.id))
})

async function load() {
    loading.value = true
    error.value = null
    const [access, accounts] = await Promise.all([getTargetAccess(props.kind, props.targetId), listUsers()])
    loading.value = false
    if (!access.ok) { error.value = access.error; return }
    members.value = access.data.members || []
    if (accounts.ok) users.value = accounts.data.users || []
}

async function setLevel(userId, level) {
    busyUserId.value = userId
    error.value = null
    const result = await setTargetAccess(props.kind, props.targetId, userId, level === 'none' ? null : level)
    busyUserId.value = null
    if (!result.ok) {
        // Re-sync the selects with the server state, then show why.
        await load()
        error.value = result.error
        return
    }
    members.value = result.data.members || []
}

async function addMember() {
    const userId = Number(newUserId.value)
    if (!userId) return
    await setLevel(userId, newLevel.value)
    newUserId.value = ''
    newLevel.value = 'view'
}

onMounted(load)
watch(() => [props.kind, props.targetId], load)

defineExpose({ reload: load })
</script>

<template>
    <div class="access-section">
        <p class="access-intro">
            {{ kind === 'project' ? $t('accounts.access.projectIntro') : $t('accounts.access.workspaceIntro') }}
        </p>

        <wa-callout v-if="error" variant="danger" size="small">
            <wa-icon slot="icon" name="circle-exclamation"></wa-icon>
            {{ resolveUiMessage(error) }}
        </wa-callout>

        <div v-if="loading" class="access-loading"><wa-spinner></wa-spinner></div>

        <ul v-else class="access-list">
            <li v-for="row in members" :key="row.user.id" class="access-row">
                <span class="access-avatar" aria-hidden="true">
                    <img v-if="row.user.avatar_url" :src="row.user.avatar_url" alt="" referrerpolicy="no-referrer" />
                    <template v-else>{{ userInitials(row.user) }}</template>
                </span>
                <span class="access-who">
                    <span class="access-name">{{ row.user.display_name || row.user.email }}</span>
                    <span class="access-email">{{ row.user.email }}</span>
                    <span v-if="row.via_workspaces?.length" class="access-via">
                        <wa-tag
                            v-for="via in row.via_workspaces"
                            :key="via.workspace_id"
                            size="small"
                            variant="neutral"
                        >
                            {{ $t('accounts.access.viaWorkspace', { workspace: via.workspace_name || via.workspace_id, level: $t(`accounts.levels.${via.level}`) }) }}
                        </wa-tag>
                    </span>
                </span>
                <wa-badge v-if="row.is_admin" variant="brand" appearance="filled-outlined">{{ $t('accounts.access.allAccess') }}</wa-badge>
                <wa-select
                    v-else
                    size="small"
                    class="access-level"
                    :value.prop="rowLevel(row)"
                    :disabled="busyUserId === row.user.id"
                    :aria-label="$t('accounts.access.levelFor', { user: row.user.email })"
                    @change.stop="setLevel(row.user.id, $event.target.value)"
                    @input.stop
                    @wa-show.stop
                    @wa-hide.stop
                    @wa-after-show.stop
                    @wa-after-hide.stop
                >
                    <wa-option v-for="option in levelOptions" :key="option.value" :value="option.value">{{ option.label }}</wa-option>
                </wa-select>
            </li>
            <li v-if="!members.length" class="access-empty">{{ $t('accounts.access.empty') }}</li>
        </ul>

        <div v-if="addableUsers.length" class="access-add">
            <wa-select
                size="small"
                class="access-add-user"
                :placeholder="$t('accounts.access.addPlaceholder')"
                :value.prop="newUserId"
                @change.stop="newUserId = $event.target.value"
                @input.stop
                @wa-show.stop
                @wa-hide.stop
                @wa-after-show.stop
                @wa-after-hide.stop
            >
                <wa-option v-for="user in addableUsers" :key="user.id" :value="String(user.id)">
                    {{ user.display_name ? $t('accounts.access.userOption', { name: user.display_name, email: user.email }) : user.email }}
                </wa-option>
            </wa-select>
            <wa-select
                size="small"
                class="access-level"
                :value.prop="newLevel"
                :aria-label="$t('accounts.access.newLevel')"
                @change.stop="newLevel = $event.target.value"
                @input.stop
                @wa-show.stop
                @wa-hide.stop
                @wa-after-show.stop
                @wa-after-hide.stop
            >
                <wa-option v-for="level in ACCESS_LEVELS" :key="level" :value="level">{{ $t(`accounts.levels.${level}`) }}</wa-option>
            </wa-select>
            <wa-button size="small" variant="brand" appearance="outlined" :disabled="!newUserId" @click="addMember">
                <wa-icon slot="start" name="user-plus"></wa-icon>
                {{ $t('accounts.access.add') }}
            </wa-button>
        </div>
        <p v-else-if="!loading && !users.some(user => user.role === 'member')" class="access-hint">
            {{ $t('accounts.access.noMembers') }}
        </p>
    </div>
</template>

<style scoped>
.access-section {
    display: flex;
    flex-direction: column;
    gap: var(--wa-space-s);
}

.access-intro,
.access-hint {
    margin: 0;
    font-size: var(--wa-font-size-s);
    color: var(--wa-color-text-quiet);
}

.access-loading {
    display: flex;
    justify-content: center;
    padding: var(--wa-space-m);
}

.access-list {
    list-style: none;
    margin: 0;
    padding: 0;
    display: flex;
    flex-direction: column;
    gap: var(--wa-space-xs);
}

.access-row {
    display: flex;
    align-items: center;
    gap: var(--wa-space-s);
    padding: var(--wa-space-2xs) 0;
}

.access-avatar {
    flex: none;
    width: 2rem;
    height: 2rem;
    border-radius: 50%;
    display: inline-flex;
    align-items: center;
    justify-content: center;
    overflow: hidden;
    font-size: var(--wa-font-size-xs);
    font-weight: var(--wa-font-weight-semibold);
    background: var(--wa-color-brand-fill-quiet);
    color: var(--wa-color-brand-on-quiet);
}

.access-avatar img {
    width: 100%;
    height: 100%;
    object-fit: cover;
}

.access-who {
    flex: 1;
    min-width: 0;
    display: flex;
    flex-direction: column;
}

.access-name {
    font-weight: var(--wa-font-weight-semibold);
    overflow: hidden;
    text-overflow: ellipsis;
}

.access-email {
    font-size: var(--wa-font-size-xs);
    color: var(--wa-color-text-quiet);
    overflow: hidden;
    text-overflow: ellipsis;
}

.access-via {
    display: flex;
    flex-wrap: wrap;
    gap: var(--wa-space-3xs);
    margin-top: var(--wa-space-3xs);
}

.access-level {
    flex: none;
    width: 9rem;
}

.access-empty {
    font-size: var(--wa-font-size-s);
    color: var(--wa-color-text-quiet);
}

.access-add {
    display: flex;
    align-items: center;
    gap: var(--wa-space-xs);
    flex-wrap: wrap;
}

.access-add-user {
    flex: 1;
    min-width: 12rem;
}
</style>
