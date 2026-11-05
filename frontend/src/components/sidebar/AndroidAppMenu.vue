<script setup>
import { computed, nextTick, onBeforeUnmount, onMounted, ref } from 'vue'
import BrandLogo from '../ui/BrandLogo.vue'
import { getAndroidAppClient } from '../../utils/androidAppClient.js'
import { watchAndroidMenuPlacement } from '../../utils/androidMenuPlacement.js'

const props = defineProps({
    forceFixed: { type: Boolean, default: false },
    backendVersion: { type: String, default: '' },
})
const target = ref(null)
const railTarget = computed(() => props.forceFixed ? null : target.value)
const state = ref(null)
const error = ref('')
const trigger = ref(null)
const dropdown = ref(null)
const confirmDialog = ref(null)
const owner = `rail-${Math.random().toString(36).slice(2)}`
const actions = new Set(['manageInstances', 'reload', 'serverHome', 'openInBrowser'])
let client, heartbeat, observer, placement, disposed = false, intersects = false

async function readState() {
    const value = await client.request('getState')
    if (!value || typeof value.version !== 'string' || typeof value.engine !== 'string' ||
        typeof value.activeInstanceId !== 'string' || !Array.isArray(value.instances) ||
        !value.instances.every(item => item && ['id', 'name', 'address'].every(key => typeof item[key] === 'string'))) {
        throw new Error('Unsupported Android app state.')
    }
    if (!disposed) state.value = value
}

function reportVisibility() {
    if (disposed) return
    placement?.refresh()
    const button = trigger.value
    const rect = button?.getBoundingClientRect()
    const visible = !!(button?.isConnected && document.visibilityState === 'visible' && intersects &&
        rect.width > 0 && rect.height > 0 && window.getComputedStyle(button).visibility === 'visible')
    client.request('menuVisibility', { owner, visible }).catch(() => {})
}

onMounted(async () => {
    client = getAndroidAppClient()
    try { await readState() } catch { return } // Ordinary browsers have no Android controls.
    await nextTick()
    if (disposed) return
    placement = watchAndroidMenuPlacement(window, element => { target.value = element })
    if (typeof IntersectionObserver !== 'undefined') {
        observer = new IntersectionObserver(entries => {
            intersects = entries.some(entry => entry.isIntersecting)
            reportVisibility()
        })
        if (trigger.value) observer.observe(trigger.value)
    } else intersects = true
    document.addEventListener('visibilitychange', reportVisibility)
    window.addEventListener('resize', reportVisibility)
    heartbeat = setInterval(reportVisibility, 2000)
    reportVisibility()
})

onBeforeUnmount(() => {
    disposed = true
    clearInterval(heartbeat)
    observer?.disconnect()
    placement?.stop()
    document.removeEventListener('visibilitychange', reportVisibility)
    window.removeEventListener('resize', reportVisibility)
    if (state.value) client.request('menuVisibility', { owner, visible: false }).catch(() => {})
})

async function send(command, args) {
    error.value = ''
    try { await client.request(command, args) }
    catch (failure) {
        if (disposed) return
        error.value = failure.message
        if (dropdown.value) dropdown.value.open = true
    }
}

async function refreshState() {
    try { await readState() }
    catch (failure) { if (!disposed) error.value = failure.message }
}

async function selectAction(event) {
    const value = event.detail?.item?.value
    if (typeof value !== 'string') return
    if (value.startsWith('instance:')) {
        const id = value.slice('instance:'.length)
        if (id !== state.value.activeInstanceId && state.value.instances.some(item => item.id === id))
            await send('switchInstance', { instanceId: id })
    } else if (value === 'forceRefresh') {
        confirmDialog.value.open = true
    } else if (actions.has(value)) await send(value)
}

async function confirmForceRefresh() {
    confirmDialog.value.open = false
    await send('forceRefresh')
}
</script>

<template>
    <template v-if="state">
        <Teleport :to="railTarget || 'body'" :disabled="!railTarget">
            <div class="android-app-menu-host" :class="{ 'android-app-menu-host--fixed': !railTarget }">
                <wa-dropdown ref="dropdown" class="android-app-menu" placement="right-start"
                    @wa-show.self="refreshState" @wa-select.self="selectAction">
                    <button ref="trigger" slot="trigger" type="button" class="rail-button" aria-label="Android app menu">
                        <BrandLogo :size="26" />
                    </button>
                    <wa-dropdown-item disabled>{{ backendVersion ? `TwiCC ${backendVersion}` : 'TwiCC' }} · Android {{ state.version }}</wa-dropdown-item>
                    <wa-divider />
                    <wa-dropdown-item v-for="instance in state.instances" :key="instance.id"
                        :value="`instance:${instance.id}`" type="checkbox"
                        :checked="instance.id === state.activeInstanceId" :disabled="instance.id === state.activeInstanceId">
                        <span class="instance-label">
                            <span>{{ instance.name }}</span>
                            <span class="instance-address">{{ instance.address }}</span>
                        </span>
                        <span v-if="instance.id === state.activeInstanceId" slot="details">Active</span>
                    </wa-dropdown-item>
                    <wa-dropdown-item value="manageInstances">
                        <wa-icon slot="icon" name="server" />Manage instances
                    </wa-dropdown-item>
                    <wa-divider />
                    <wa-dropdown-item value="reload"><wa-icon slot="icon" name="rotate-right" />Reload page</wa-dropdown-item>
                    <wa-dropdown-item value="forceRefresh"><wa-icon slot="icon" name="arrows-rotate" />Force refresh (keep login)</wa-dropdown-item>
                    <wa-dropdown-item v-if="!railTarget" value="serverHome"><wa-icon slot="icon" name="house" />Server home</wa-dropdown-item>
                    <wa-dropdown-item value="openInBrowser"><wa-icon slot="icon" name="arrow-up-right-from-square" />Open in browser</wa-dropdown-item>
                    <wa-callout v-if="error" variant="danger" role="alert">{{ error }}</wa-callout>
                </wa-dropdown>
            </div>
        </Teleport>
        <wa-dialog ref="confirmDialog" label="Force refresh?" class="android-refresh-dialog">
            Reload this page and bypass its resource cache. Your login and preferences stay saved. Unsaved text may be lost.
            <wa-button slot="footer" appearance="plain" @click="confirmDialog.open = false">Cancel</wa-button>
            <wa-button slot="footer" variant="brand" @click="confirmForceRefresh">Refresh</wa-button>
        </wa-dialog>
    </template>
</template>

<style scoped>
.android-app-menu-host {
    display: contents;
}
.android-app-menu-host--fixed {
    display: block;
    position: fixed;
    top: calc(var(--panel-gap, 4px) + var(--rail-card-padding, 4px) + var(--divider-size, 1px));
    left: calc(var(--panel-gap, 4px) + var(--rail-card-padding, 4px) + var(--divider-size, 1px));
    z-index: 10001; /* Keep the app menu available above the connecting overlay. */
    --rail-button-bg: var(--wa-color-surface-default);
}

.android-app-menu {
    flex: none;
}
.android-app-menu::part(menu) {
    max-width: min(26rem, calc(100vw - 2rem));
    max-height: calc(100dvh - 2rem);
    overflow-y: auto;
}
.instance-label {
    display: flex;
    flex-direction: column;
    min-width: 0;
}
.instance-address {
    font-size: var(--wa-font-size-xs);
    color: var(--wa-color-text-quiet);
    overflow: hidden;
    text-overflow: ellipsis;
}
.android-app-menu wa-callout {
    margin: 0.5rem;
}
.android-refresh-dialog {
    --width: min(480px, calc(100vw - 2rem));
}
</style>
