<script setup>
// AdminUsersView.vue — multi-user administration (route /admin/users, admins only).
//
// Lists the accounts, opens the create/edit dialog, and shows the selected
// member's access matrix. Reloads on the `twicc:accounts-changed` window event
// (fired by the WebSocket `accounts_changed` handler) so two admins stay in sync.
import { computed, onBeforeUnmount, onMounted, ref } from 'vue'
import { useRouter } from 'vue-router'
import { resolveUiMessage } from '../i18n/uiMessages.js'
import { useAuthStore } from '../stores/auth'
import { listUsers } from '../utils/accountsApi'
import { userInitials } from '../utils/accountsDisplay'
import UserEditDialog from '../components/accounts/UserEditDialog.vue'
import UserAccessMatrix from '../components/accounts/UserAccessMatrix.vue'

const router = useRouter()
const authStore = useAuthStore()

const users = ref([])
const loading = ref(false)
const error = ref('')
const selectedId = ref(null)
const editingUser = ref(null)
const editDialogRef = ref(null)

const selectedUser = computed(() => users.value.find(user => user.id === selectedId.value) || null)
const selfId = computed(() => (authStore.principal?.kind === 'user' ? authStore.principal.id : null))
const isOwner = computed(() => authStore.principal?.kind === 'owner')

async function load() {
    loading.value = true
    const result = await listUsers()
    loading.value = false
    if (!result.ok) {
        error.value = result.error
        return
    }
    error.value = ''
    users.value = result.data.users || []
    if (selectedId.value && !selectedUser.value) selectedId.value = null
}

function upsert(user) {
    if (!user) return
    const index = users.value.findIndex(existing => existing.id === user.id)
    if (index >= 0) users.value.splice(index, 1, user)
    else users.value.push(user)
}

function onSaved(user) {
    upsert(user)
    // The first account switches the instance to multi-user mode.
    authStore.checkAuthOnce()
}

function onDeleted(userId) {
    users.value = users.value.filter(user => user.id !== userId)
    if (selectedId.value === userId) selectedId.value = null
    authStore.checkAuthOnce()
}

function openCreate() {
    editingUser.value = null
    // The dialog reads its `user` prop in open(): wait for it to settle.
    requestAnimationFrame(() => editDialogRef.value?.open())
}

function openEdit(user) {
    editingUser.value = user
    requestAnimationFrame(() => editDialogRef.value?.open())
}

function signInMethods(user) {
    const methods = []
    if (user.has_password) methods.push('password')
    if (user.google_linked) methods.push('google')
    return methods
}

function goHome() {
    router.push({ name: 'home' })
}

onMounted(() => {
    load()
    window.addEventListener('twicc:accounts-changed', load)
})

onBeforeUnmount(() => {
    window.removeEventListener('twicc:accounts-changed', load)
})
</script>

<template>
    <div class="admin-page">
        <div class="admin-view">
            <header class="admin-header">
                <wa-button size="small" appearance="plain" @click="goHome">
                    <wa-icon slot="start" name="arrow-left"></wa-icon>
                    {{ $t('accounts.admin.back') }}
                </wa-button>
                <h1 class="admin-title">
                    <wa-icon name="users-gear"></wa-icon>
                    {{ $t('accounts.admin.title') }}
                </h1>
                <wa-button size="small" variant="brand" @click="openCreate">
                    <wa-icon slot="start" name="user-plus"></wa-icon>
                    {{ $t('accounts.admin.newAccount') }}
                </wa-button>
            </header>

            <wa-callout variant="brand" size="small" class="admin-explain">
                <wa-icon slot="icon" name="circle-info"></wa-icon>
                <ul class="admin-explain-list">
                    <li>{{ $t('accounts.admin.explainAdmin') }}</li>
                    <li>{{ $t('accounts.admin.explainMember') }}</li>
                    <li>{{ $t('accounts.admin.explainLevels') }}</li>
                    <li v-if="isOwner">{{ $t('accounts.admin.explainOwner') }}</li>
                </ul>
            </wa-callout>

            <wa-callout variant="warning" size="small">
                <wa-icon slot="icon" name="triangle-exclamation"></wa-icon>
                {{ $t('accounts.admin.honestLimits') }}
            </wa-callout>

            <wa-callout v-if="error" variant="danger" size="small">
                <wa-icon slot="icon" name="circle-exclamation"></wa-icon>
                {{ resolveUiMessage(error) }}
            </wa-callout>

            <div v-if="loading && !users.length" class="admin-loading"><wa-spinner></wa-spinner></div>

            <div v-else-if="!users.length" class="admin-empty">
                <p>{{ $t('accounts.admin.empty') }}</p>
                <wa-button variant="brand" @click="openCreate">
                    <wa-icon slot="start" name="user-plus"></wa-icon>
                    {{ $t('accounts.admin.createFirst') }}
                </wa-button>
            </div>

            <div v-else class="admin-grid">
                <section class="admin-users">
                    <table class="users-table">
                        <thead>
                            <tr>
                                <th>{{ $t('accounts.fields.account') }}</th>
                                <th>{{ $t('accounts.fields.role') }}</th>
                                <th>{{ $t('accounts.fields.signIn') }}</th>
                                <th>{{ $t('accounts.fields.lastLogin') }}</th>
                                <th><span class="visually-hidden">{{ $t('common.actions.edit') }}</span></th>
                            </tr>
                        </thead>
                        <tbody>
                            <tr
                                v-for="user in users"
                                :key="user.id"
                                class="user-row"
                                :class="{ 'user-row--selected': user.id === selectedId, 'user-row--inactive': !user.is_active }"
                                @click="selectedId = user.id"
                            >
                                <td>
                                    <span class="user-cell">
                                        <span class="user-avatar" aria-hidden="true">
                                            <img v-if="user.avatar_url" :src="user.avatar_url" alt="" referrerpolicy="no-referrer" />
                                            <template v-else>{{ userInitials(user) }}</template>
                                        </span>
                                        <span class="user-who">
                                            <span class="user-name">{{ user.display_name || user.email }}</span>
                                            <span class="user-email">{{ user.email }}</span>
                                        </span>
                                    </span>
                                </td>
                                <td>
                                    <wa-badge :variant="user.role === 'admin' ? 'brand' : 'neutral'" appearance="filled-outlined">
                                        {{ $t(`accounts.roles.${user.role}`) }}
                                    </wa-badge>
                                    <wa-badge v-if="!user.is_active" variant="danger" appearance="outlined" class="user-status">
                                        {{ $t('accounts.status.disabled') }}
                                    </wa-badge>
                                </td>
                                <td>
                                    <span class="user-methods">
                                    <wa-tag v-for="method in signInMethods(user)" :key="method" size="small">
                                        <wa-icon v-if="method === 'google'" slot="start" family="brands" name="google"></wa-icon>
                                        {{ $t(`accounts.methods.${method}`) }}
                                    </wa-tag>
                                    <span v-if="!signInMethods(user).length" class="user-muted">{{ $t('accounts.methods.googlePending') }}</span>
                                    </span>
                                </td>
                                <td>
                                    <wa-relative-time v-if="user.last_login_at" :date.prop="new Date(user.last_login_at)" numeric="always" sync></wa-relative-time>
                                    <span v-else class="user-muted">{{ $t('accounts.status.neverSignedIn') }}</span>
                                </td>
                                <td class="user-actions">
                                    <wa-button size="small" appearance="plain" :aria-label="$t('common.actions.edit')" @click.stop="openEdit(user)">
                                        <wa-icon name="pen"></wa-icon>
                                    </wa-button>
                                </td>
                            </tr>
                        </tbody>
                    </table>
                </section>

                <section class="admin-detail">
                    <template v-if="selectedUser">
                        <h2 class="detail-title">{{ $t('accounts.matrix.title', { user: selectedUser.display_name || selectedUser.email }) }}</h2>
                        <p v-if="selectedUser.role === 'admin'" class="detail-muted">{{ $t('accounts.matrix.adminSeesAll') }}</p>
                        <UserAccessMatrix v-else :user="selectedUser" @saved="upsert" />
                    </template>
                    <p v-else class="detail-muted">{{ $t('accounts.matrix.selectHint') }}</p>
                </section>
            </div>
        </div>

        <UserEditDialog
            ref="editDialogRef"
            :user="editingUser"
            :self-id="selfId"
            @saved="onSaved"
            @deleted="onDeleted"
        />
    </div>
</template>

<style scoped>
.admin-page {
    min-height: 100dvh;
    background: var(--canvas-background);
    overflow-y: auto;
}

.admin-view {
    max-width: 1200px;
    margin: 0 auto;
    padding: var(--wa-space-l) var(--wa-space-m);
    display: flex;
    flex-direction: column;
    gap: var(--wa-space-m);
}

.admin-header {
    display: flex;
    align-items: center;
    gap: var(--wa-space-s);
}

.admin-title {
    flex: 1;
    margin: 0;
    font-size: var(--wa-font-size-xl);
    display: flex;
    align-items: center;
    gap: var(--wa-space-xs);
}

.admin-explain-list {
    margin: 0;
    padding-left: var(--wa-space-m);
    display: flex;
    flex-direction: column;
    gap: var(--wa-space-3xs);
}

.admin-loading,
.admin-empty {
    display: flex;
    flex-direction: column;
    align-items: center;
    gap: var(--wa-space-s);
    padding: var(--wa-space-xl);
    color: var(--wa-color-text-quiet);
}

.admin-grid {
    display: grid;
    grid-template-columns: minmax(0, 3fr) minmax(0, 2fr);
    gap: var(--wa-space-l);
    align-items: start;
}

@media (width < 900px) {
    .admin-grid {
        grid-template-columns: minmax(0, 1fr);
    }
}

.users-table {
    width: 100%;
    border-collapse: collapse;
    font-size: var(--wa-font-size-s);
}

.users-table th {
    text-align: left;
    font-weight: var(--wa-font-weight-semibold);
    color: var(--wa-color-text-quiet);
    padding: var(--wa-space-2xs) var(--wa-space-xs);
    border-bottom: var(--wa-border-width-s) solid var(--wa-color-surface-border);
}

.users-table td {
    padding: var(--wa-space-xs);
    border-bottom: var(--wa-border-width-s) solid var(--wa-color-surface-border);
    vertical-align: middle;
}

.user-row {
    cursor: pointer;
}

.user-row:hover {
    background: var(--wa-color-neutral-fill-quiet);
}

.user-row--selected {
    background: var(--wa-color-brand-fill-quiet);
}

.user-row--inactive .user-who {
    opacity: 0.6;
}

.user-cell {
    display: flex;
    align-items: center;
    gap: var(--wa-space-xs);
}

.user-avatar {
    flex: none;
    width: 2rem;
    height: 2rem;
    border-radius: 50%;
    overflow: hidden;
    display: inline-flex;
    align-items: center;
    justify-content: center;
    font-size: var(--wa-font-size-xs);
    font-weight: var(--wa-font-weight-semibold);
    background: var(--wa-color-brand-fill-quiet);
    color: var(--wa-color-brand-on-quiet);
}

.user-avatar img {
    width: 100%;
    height: 100%;
    object-fit: cover;
}

.user-who {
    display: flex;
    flex-direction: column;
    min-width: 0;
}

.user-name {
    font-weight: var(--wa-font-weight-semibold);
}

.user-email,
.user-muted,
.detail-muted {
    font-size: var(--wa-font-size-xs);
    color: var(--wa-color-text-quiet);
}

.user-status {
    margin-left: var(--wa-space-2xs);
}

.user-methods {
    display: flex;
    flex-wrap: wrap;
    gap: var(--wa-space-3xs);
}

.user-actions {
    text-align: right;
}

.detail-title {
    margin: 0 0 var(--wa-space-s);
    font-size: var(--wa-font-size-l);
}

.visually-hidden {
    position: absolute;
    width: 1px;
    height: 1px;
    overflow: hidden;
    clip: rect(0 0 0 0);
}
</style>
