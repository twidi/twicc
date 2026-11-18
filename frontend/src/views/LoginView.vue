<script setup>
import { resolveUiMessage } from '../i18n/uiMessages.js'
import { localUiMessage } from '../i18n/uiMessages.js'
import { computed, ref, onMounted, onUnmounted, nextTick } from 'vue'
import { useRouter } from 'vue-router'
import { useAuthStore } from '../stores/auth'
import { markAppNavigation } from '../utils/appNavigation'
import BrandLogo from '../components/ui/BrandLogo.vue'

const authStore = useAuthStore()
const router = useRouter()

const password = ref('')
const email = ref('')
const error = ref('')
const loading = ref(false)
const passwordInput = ref(null)
const emailInput = ref(null)

// Multi-user sign-in options (all false on a single-user instance, which keeps
// the historical password-only screen).
const googleEnabled = computed(() => authStore.googleEnabled)
const accountsEnabled = computed(() => authStore.accountsEnabled)
// 'account' (email + password) or 'owner' (instance password).
const mode = ref(authStore.accountsEnabled ? 'account' : 'owner')
const showAccountForm = computed(() => accountsEnabled.value && mode.value === 'account')
// Switching forms is only offered when both ways exist.
const canSwitchMode = computed(() => accountsEnabled.value && authStore.ownerPassword)

const GOOGLE_ERROR_KEYS = {
    cancelled: 'auth.login.googleErrors.cancelled',
    invalid_state: 'auth.login.googleErrors.invalidState',
    no_account: 'auth.login.googleErrors.noAccount',
    account_disabled: 'auth.login.googleErrors.accountDisabled',
    identity_mismatch: 'auth.login.googleErrors.identityMismatch',
    email_not_verified: 'auth.login.googleErrors.emailNotVerified',
    rate_limited: 'auth.login.googleErrors.rateLimited',
}

function toggleMode() {
    mode.value = mode.value === 'account' ? 'owner' : 'account'
    error.value = ''
    focusFirstInput()
}

function signInWithGoogle() {
    const redirect = router.currentRoute.value.query.redirect || '/'
    markAppNavigation()
    window.location.href = `/api/auth/google/start/?next=${encodeURIComponent(redirect)}`
}

/**
 * Redirect to the originally requested page (from ?redirect=) or home.
 *
 * Uses a full page navigation instead of router.replace() so that main.js
 * re-runs with an authenticated session — that's where bootstrap data
 * (settings, workspaces, terminal config, snippets, model registry) is
 * fetched and applied before the app mounts. Without this reload, the
 * app would mount without that data and several stores would be empty.
 */
function redirectAway() {
    const redirect = router.currentRoute.value.query.redirect || '/'
    // An app-initiated page load: the uploads `beforeunload` handler must not ask to confirm it.
    markAppNavigation()
    window.location.href = redirect
}

// Periodically re-check auth while on the login page.
// Handles two scenarios:
// 1. Backend wasn't ready when we landed here, now it's ready and has no password
// 2. Password was removed from config while we're on the login page
let recheckInterval = null

async function focusPasswordInput() {
    // Wait for Vue's render, then for the wa-input (Lit-based custom
    // element) to be fully upgraded — at mount time nextTick alone isn't
    // enough, and a bare `autofocus` attribute is silently dropped.
    await nextTick()
    if (passwordInput.value?.updateComplete) {
        await passwordInput.value.updateComplete
    }
    passwordInput.value?.focus()
}

async function focusFirstInput() {
    await nextTick()
    const target = showAccountForm.value && !email.value ? emailInput.value : passwordInput.value
    if (target?.updateComplete) await target.updateComplete
    target?.focus()
}

onMounted(async () => {
    const googleError = authStore.consumeGoogleError()
    if (googleError) {
        error.value = localUiMessage(GOOGLE_ERROR_KEYS[googleError] || 'auth.login.googleErrors.generic')
    }
    focusFirstInput()

    recheckInterval = setInterval(async () => {
        await authStore.checkAuthOnce()
        if (!authStore.needsLogin) {
            redirectAway()
        }
    }, 3000)
})

onUnmounted(() => {
    if (recheckInterval) {
        clearInterval(recheckInterval)
        recheckInterval = null
    }
})

async function handleSubmit() {
    error.value = ''
    loading.value = true
    let succeeded = false

    try {
        // Short-circuits on a single-user instance (no account form, no email).
        const loginEmail = authStore.accountsEnabled && mode.value === 'account' ? email.value.trim() : ''
        const result = await authStore.login(password.value, loginEmail)
        if (result.success) {
            succeeded = true
            redirectAway()
            return
        }
        error.value = result.errorMessage || result.error
        password.value = ''
    } finally {
        loading.value = false
        // Re-focus the input on any failure path (bad password, network
        // error, …). `focusPasswordInput` waits for the re-render so the
        // just-reset `disabled` is gone before calling focus().
        if (!succeeded) {
            await focusPasswordInput()
        }
    }
}
</script>

<template>
    <div class="login-backdrop">
        <wa-card class="login-card">
            <div class="login-header">
                <BrandLogo :size="64" animated />
                <!-- eslint-disable-next-line @intlify/vue-i18n/no-raw-text -- TwiCC is the product name. -->
                <h1 class="login-title">TwiCC</h1>
                <p class="login-subtitle">{{ showAccountForm ? $t('auth.login.accountRequired') : $t('auth.login.passwordRequired') }}</p>
            </div>

            <wa-divider></wa-divider>

            <div class="login-form">
                <wa-button
                    v-if="googleEnabled"
                    variant="neutral"
                    appearance="outlined"
                    size="medium"
                    class="login-button"
                    @click="signInWithGoogle"
                >
                    <wa-icon slot="start" family="brands" name="google"></wa-icon>
                    {{ $t('auth.login.signInWithGoogle') }}
                </wa-button>

                <div v-if="googleEnabled" class="login-or">{{ $t('auth.login.or') }}</div>

                <form @submit.prevent="handleSubmit" class="login-form">
                    <div v-if="showAccountForm" class="form-group">
                        <label class="form-label">{{ $t('auth.login.emailLabel') }}</label>
                        <wa-input
                            ref="emailInput"
                            type="email"
                            autocomplete="username"
                            :placeholder="$t('auth.login.emailPlaceholder')"
                            :value="email"
                            @input="email = $event.target.value"
                            @wa-input="email = $event.target.value"
                            :disabled="loading"
                            size="small"
                        >
                            <wa-icon slot="start" name="envelope" variant="solid"></wa-icon>
                        </wa-input>
                    </div>

                    <div class="form-group">
                        <label class="form-label">{{ showAccountForm ? $t('auth.login.accountPasswordLabel') : $t('auth.login.passwordLabel') }}</label>
                        <wa-input
                            ref="passwordInput"
                            type="password"
                            autocomplete="current-password"
                            :placeholder="$t('auth.login.passwordPlaceholder')"
                            :value="password"
                            @input="password = $event.target.value"
                            @wa-input="password = $event.target.value"
                            :disabled="loading"
                            size="small"
                        >
                            <wa-icon slot="start" name="lock" variant="solid"></wa-icon>
                        </wa-input>
                    </div>

                    <wa-button
                        type="submit"
                        variant="brand"
                        size="medium"
                        :loading="loading"
                        :disabled="!password || (showAccountForm && !email) || loading"
                        class="login-button"
                    >
                        <wa-icon slot="start" name="right-to-bracket"></wa-icon>
                        {{ $t('auth.login.signIn') }}
                    </wa-button>
                </form>

                <wa-callout v-if="error" variant="danger" size="small" class="login-error">
                    <wa-icon slot="icon" name="circle-exclamation"></wa-icon>
                    {{ resolveUiMessage(error) }}
                </wa-callout>

                <a v-if="canSwitchMode" href="#" class="login-switch" @click.prevent="toggleMode">
                    {{ showAccountForm ? $t('auth.login.useInstancePassword') : $t('auth.login.useAccount') }}
                </a>
            </div>
        </wa-card>
    </div>
</template>

<style scoped>
.login-backdrop {
    position: fixed;
    inset: 0;
    display: flex;
    align-items: center;
    justify-content: center;
    /* Full canvas (auras included) — a fixed layer above everything would hide body::before. */
    background: var(--canvas-background);
    z-index: 9999;
}

.login-card {
    width: 100%;
    max-width: 380px;
    --padding: var(--wa-space-xl);
}

.login-header {
    text-align: center;
    display: flex;
    flex-direction: column;
    align-items: center;
    gap: var(--wa-space-2xs);
}

.login-title {
    font-size: var(--wa-font-size-xl);
    font-weight: 700;
    margin: 0;
    letter-spacing: 0.05em;
}

.login-subtitle {
    font-size: var(--wa-font-size-s);
    color: var(--wa-color-text-quiet);
    margin: 0;
}

.login-form {
    display: flex;
    flex-direction: column;
    gap: var(--wa-space-m);
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

.login-error {
    margin: 0;
}

.login-or {
    text-align: center;
    font-size: var(--wa-font-size-s);
    color: var(--wa-color-text-quiet);
}

.login-switch {
    text-align: center;
    font-size: var(--wa-font-size-s);
}

.login-button {
    width: 100%;
}

.login-button::part(base) {
    width: 100%;
}
</style>
