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
        <wa-card class="login-card">
            <div class="login-header">
                <wa-icon name="terminal" class="login-icon"></wa-icon>
                <h1 class="login-title">TWICC</h1>
                <p class="login-subtitle">Password required to continue</p>
            </div>

            <wa-divider></wa-divider>

            <form @submit.prevent="handleSubmit" class="login-form">
                <div class="form-group">
                    <label class="form-label">Password</label>
                    <wa-input
                        type="password"
                        placeholder="Enter password"
                        :value="password"
                        @input="password = $event.target.value"
                        @wa-input="password = $event.target.value"
                        autofocus
                        :disabled="loading"
                        size="small"
                    >
                        <wa-icon slot="prefix" name="lock" variant="solid"></wa-icon>
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
                    <wa-icon slot="prefix" name="right-to-bracket"></wa-icon>
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

.login-icon {
    font-size: var(--wa-font-size-2xl);
    color: var(--wa-color-brand);
}

.login-title {
    font-size: var(--wa-font-size-xl);
    font-weight: 700;
    color: var(--wa-color-text-loud);
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
