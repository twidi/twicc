<script setup>
/**
 * ProviderAuthToastContent — rich content for the persistent
 * "<provider> CLI not authenticated" toast.
 *
 * Includes:
 * - "Launch in terminal" — queues the login command for the global ("all
 *   projects") terminal view and navigates there.
 * - "Check again" — asks the backend to re-check the auth state right now
 *   (instead of waiting for the next periodic tick).
 * - "Disable provider" — turns the provider off via the shared
 *   ``useProviderActivation`` composable. Hidden when the same gate that
 *   greys out the Settings switch refuses the disable (last provider
 *   standing, active sessions, lifecycle transition).
 */
import { computed, ref } from 'vue'
import { useRouter } from 'vue-router'
import { useTerminalCommandStore } from '../../stores/terminalCommand'
import { getProviderHelpers, getProviderLabel } from '../../providers'
import { useProviderActivation } from '../../composables/useProviderActivation'
import { toast } from '../../composables/useToast'

const props = defineProps({
    /** Notivue item reference — passed by CustomNotification (unused here, but standard signature) */
    item: {
        type: Object,
        default: null,
    },
    /** Wire key of the provider this toast belongs to. */
    provider: {
        type: String,
        required: true,
    },
    /** Pre-resolved login command string. */
    loginCommand: {
        type: String,
        required: true,
    },
})

const terminalCommandStore = useTerminalCommandStore()
const router = useRouter()
const { canDisableProvider, setProviderEnabled } = useProviderActivation()

// Disable the button briefly after a click to avoid spam-clicking while the
// backend round-trip happens.
const checking = ref(false)

function checkAgain() {
    if (checking.value) return
    checking.value = true
    getProviderHelpers(props.provider)?.requestAuthRecheck()
    setTimeout(() => {
        checking.value = false
    }, 1500)
}

function launchInTerminal() {
    terminalCommandStore.request('global', props.loginCommand)
    router.push({ name: 'projects-terminal' })
}

const canDisable = computed(() => canDisableProvider(props.provider))

function disableProvider() {
    setProviderEnabled(props.provider, false)
    toast.success(`${getProviderLabel(props.provider)} provider disabled`, { duration: 2000 })
    // This toast disappears on its own: the provider leaves
    // ``enabledProviders``, which flips the ``shouldShow`` watcher in
    // App.vue and pushes the toast out.
}

function copyLoginCommand() {
    navigator.clipboard.writeText(props.loginCommand)
    toast.success('Command copied to clipboard', { duration: 2000 })
}
</script>

<template>
    <div class="provider-auth-toast-content">
        <p class="provider-auth-toast-message">
            Run <code class="copyable" title="Click to copy" @click="copyLoginCommand">{{ loginCommand }}</code> to enable sending messages.
        </p>
        <div class="provider-auth-toast-actions">
            <wa-button
                v-if="canDisable"
                size="small"
                variant="danger"
                appearance="outlined"
                @click="disableProvider"
            >
                <wa-icon slot="start" name="power-off"></wa-icon>
                Disable provider
            </wa-button>
            <wa-button size="small" variant="brand" appearance="outlined" @click="launchInTerminal">
                <wa-icon slot="start" name="terminal"></wa-icon>
                Launch in terminal
            </wa-button>
            <wa-button size="small" variant="brand" appearance="outlined" :disabled="checking" @click="checkAgain">
                Check again
            </wa-button>
        </div>
    </div>
</template>

<style scoped>
.provider-auth-toast-content {
    display: flex;
    flex-direction: column;
    gap: var(--wa-space-s);
    margin-top: var(--wa-space-xs);
}

.provider-auth-toast-message {
    margin: 0;
}

.provider-auth-toast-message code {
    font-family: var(--wa-font-family-code);
    font-size: 0.95em;
    padding: 0 var(--wa-space-3xs);
    background: var(--wa-color-warning-fill-normal);
    color: var(--wa-color-warning-on-normal);
    border-radius: var(--wa-border-radius-s);
}

.provider-auth-toast-message code.copyable {
    cursor: pointer;
}

.provider-auth-toast-actions {
    display: flex;
    justify-content: flex-end;
    gap: var(--wa-space-xs);
    flex-wrap: wrap;
}
</style>
