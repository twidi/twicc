<script setup>
import { ref } from 'vue'
import { useRouter } from 'vue-router'
import { useAuthStore } from '../stores/auth'

const authStore = useAuthStore()
const router = useRouter()

const password = ref('')
const error = ref('')
const loading = ref(false)

async function handleSubmit() {
    error.value = ''
    loading.value = true

    try {
        const result = await authStore.login(password.value)
        if (result.success) {
            // Redirect to the originally requested page, or home
            const redirect = router.currentRoute.value.query.redirect || '/'
            router.replace(redirect)
        } else {
            error.value = result.error
            password.value = ''
        }
    } finally {
        loading.value = false
    }
}
</script>

<template>
    <div class="login-backdrop">
        <div class="login-dialog">
            <div class="login-header">
                <h1 class="login-title">TWICC</h1>
                <p class="login-subtitle">Password required</p>
            </div>

            <form @submit.prevent="handleSubmit" class="login-form">
                <wa-input
                    type="password"
                    placeholder="Enter password"
                    :value="password"
                    @input="password = $event.target.value"
                    @wa-input="password = $event.target.value"
                    autofocus
                    :disabled="loading"
                    size="large"
                    class="login-input"
                >
                    <wa-icon slot="prefix" name="lock" variant="solid"></wa-icon>
                </wa-input>

                <div v-if="error" class="login-error">
                    {{ error }}
                </div>

                <wa-button
                    type="submit"
                    variant="brand"
                    size="large"
                    :loading="loading"
                    :disabled="!password || loading"
                    class="login-button"
                >
                    Sign in
                </wa-button>
            </form>
        </div>
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

.login-dialog {
    background: var(--wa-color-surface-raised);
    border: 1px solid var(--wa-color-neutral-border-quiet);
    border-radius: var(--wa-border-radius-xl);
    padding: var(--wa-space-2xl) var(--wa-space-2xl) var(--wa-space-xl);
    width: 100%;
    max-width: 360px;
    box-shadow: var(--wa-shadow-lg);
}

.login-header {
    text-align: center;
    margin-bottom: var(--wa-space-xl);
}

.login-title {
    font-size: var(--wa-font-size-xl);
    font-weight: 700;
    color: var(--wa-color-text-normal);
    margin: 0 0 var(--wa-space-2xs);
    letter-spacing: 0.05em;
}

.login-subtitle {
    font-size: var(--wa-font-size-sm);
    color: var(--wa-color-text-subtle);
    margin: 0;
}

.login-form {
    display: flex;
    flex-direction: column;
    gap: var(--wa-space-md);
}

.login-input {
    width: 100%;
}

.login-input::part(base) {
    width: 100%;
}

.login-error {
    color: var(--wa-color-danger);
    font-size: var(--wa-font-size-sm);
    text-align: center;
    padding: var(--wa-space-2xs) var(--wa-space-sm);
    background: var(--wa-color-danger-fill-quiet);
    border-radius: var(--wa-border-radius-md);
}

.login-button {
    width: 100%;
}

.login-button::part(base) {
    width: 100%;
}
</style>
