<script setup>
// UserMenu.vue — the signed-in identity in the settings footer: avatar/initials,
// name, role badge, the Administration entry (admins) and Sign out.
//
// Shown whenever the identity is known. The owner of a single-user install sees
// "Administration" too: it is where the first account gets created.
import { computed, useId } from 'vue'
import { useRouter } from 'vue-router'
import { useI18n } from 'vue-i18n'
import { useAuthStore } from '../../stores/auth'
import { userInitials } from '../../utils/accountsDisplay'
import AppTooltip from '../ui/AppTooltip.vue'

const emit = defineEmits(['navigate'])

const { t } = useI18n()
const router = useRouter()
const authStore = useAuthStore()

const principal = computed(() => authStore.principal)
const displayName = computed(() => {
    const p = principal.value
    if (!p) return ''
    if (p.kind === 'owner') return t('accounts.menu.owner')
    return p.display_name || p.email
})
const roleLabel = computed(() => (principal.value?.is_admin ? t('accounts.roles.admin') : t('accounts.roles.member')))
const canSignOut = computed(() => authStore.passwordRequired && authStore.authenticated)
const initials = computed(() => (principal.value?.kind === 'owner' ? '★' : userInitials(principal.value)))

const adminButtonId = useId()
const logoutButtonId = useId()

function openAdmin() {
    emit('navigate')
    router.push({ name: 'admin-users' })
}

function signOut() {
    emit('navigate')
    router.push({ name: 'logout' })
}
</script>

<template>
    <span v-if="principal" class="user-menu">
        <span class="user-menu-avatar" aria-hidden="true">
            <img v-if="principal.avatar_url" :src="principal.avatar_url" alt="" referrerpolicy="no-referrer" />
            <template v-else>{{ initials }}</template>
        </span>
        <span class="user-menu-name" :title="principal.email || displayName">{{ displayName }}</span>
        <wa-badge :variant="principal.is_admin ? 'brand' : 'neutral'" appearance="outlined" class="user-menu-role">{{ roleLabel }}</wa-badge>
        <wa-button
            v-if="authStore.isAdmin"
            :id="adminButtonId"
            appearance="plain"
            size="small"
            @click="openAdmin"
        >
            <wa-icon name="users-gear" :label="$t('accounts.menu.administration')"></wa-icon>
        </wa-button>
        <AppTooltip v-if="authStore.isAdmin" :for="adminButtonId">{{ $t('accounts.menu.administration') }}</AppTooltip>
        <wa-button
            v-if="canSignOut"
            :id="logoutButtonId"
            class="logout-button"
            variant="danger"
            appearance="plain"
            size="small"
            @click="signOut"
        >
            <wa-icon name="right-from-bracket" :label="$t('auth.logout')"></wa-icon>
        </wa-button>
        <AppTooltip v-if="canSignOut" :for="logoutButtonId">{{ $t('auth.logout') }}</AppTooltip>
    </span>
</template>

<style scoped>
.user-menu {
    display: inline-flex;
    align-items: center;
    gap: var(--wa-space-2xs);
    margin-left: auto;
    min-width: 0;
}

.user-menu-avatar {
    flex: none;
    width: 1.5rem;
    height: 1.5rem;
    border-radius: 50%;
    overflow: hidden;
    display: inline-flex;
    align-items: center;
    justify-content: center;
    font-size: var(--wa-font-size-2xs);
    font-weight: var(--wa-font-weight-semibold);
    background: var(--wa-color-brand-fill-quiet);
    color: var(--wa-color-brand-on-quiet);
}

.user-menu-avatar img {
    width: 100%;
    height: 100%;
    object-fit: cover;
}

.user-menu-name {
    max-width: 12rem;
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
}

.user-menu-role {
    font-size: var(--wa-font-size-2xs);
}
</style>
