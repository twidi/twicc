<script setup>
import { ref, onMounted, onUnmounted, nextTick } from 'vue'
import { useRouter } from 'vue-router'
import { useAuthStore } from '../stores/auth'
import BrandLogo from '../components/ui/BrandLogo.vue'

const authStore = useAuthStore()
const router = useRouter()

const password = ref('')
const error = ref('')
const loading = ref(false)
const passwordInput = ref(null)

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

onMounted(async () => {
    focusPasswordInput()

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
        const result = await authStore.login(password.value)
        if (result.success) {
            succeeded = true
            redirectAway()
            return
        }
        error.value = result.error
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
                <h1 class="login-title">TwiCC</h1>
                <p class="login-subtitle">Password required to continue</p>
            </div>

            <wa-divider></wa-divider>

            <form @submit.prevent="handleSubmit" class="login-form">
                <div class="form-group">
                    <label class="form-label">Password</label>
                    <wa-input
                        ref="passwordInput"
                        type="password"
                        placeholder="Enter password"
                        :value="password"
                        @input="password = $event.target.value"
                        @wa-input="password = $event.target.value"
                        :disabled="loading"
                        size="small"
                    >
                        <wa-icon slot="start" name="lock" variant="solid"></wa-icon>
                    </wa-input>
                </div>

                <wa-callout v-if="error" variant="danger" appearance="filled" size="small" class="login-error">
                    <wa-icon slot="icon" name="circle-exclamation"></wa-icon>
                    {{ error }}
                </wa-callout>

                <wa-button
                    type="submit"
                    variant="brand"
                    size="medium"
                    :loading="loading"
                    :disabled="!password || loading"
                    class="login-button"
                >
                    <wa-icon slot="start" name="right-to-bracket"></wa-icon>
                    Sign in
                </wa-button>
            </form>
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
    background: var(--wa-color-surface-default);
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

.login-button {
    width: 100%;
}

.login-button::part(base) {
    width: 100%;
}
</style>
