<script setup>
/**
 * McpSettings - the "External MCP" settings section: dedicated origin,
 * enablement, the copyable endpoint, and the entry point to the connection
 * manager. Structure follows the Sharing and Peers sections of
 * SettingsPopover: daily actions first, then set-once configuration.
 */
import { ref, computed, watch, onMounted } from 'vue'
import { useMcpStore } from '../../stores/mcp'
import HelpFeatureLink from '../help/HelpFeatureLink.vue'
import McpProtection from './McpProtection.vue'

const store = useMcpStore()
const url = ref('')
const saving = ref(false)
const passwordConfigured = computed(() => store.config.passwordConfigured === true)
const enabled = computed(() => passwordConfigured.value && store.config.externalMcpEnabled === true)
const hasActions = computed(() => enabled.value || store.connections.length > 0 || store.pendingRequests.length > 0)
const applied = computed(() => url.value.trim() === (store.config.mcpBaseUrl || ''))
const endpoint = computed(() => `${store.config.mcpBaseUrl}/mcp`)

watch(() => store.config.mcpBaseUrl, (value, previous) => {
    if (url.value.trim() === (previous || '')) url.value = value || ''
})

onMounted(async () => {
    await store.refresh()
    if (!url.value) url.value = store.config.mcpBaseUrl || ''
})

async function configure(values) {
    saving.value = true
    const ok = await store.act('configure', values)
    if (ok) url.value = store.config.mcpBaseUrl || ''
    saving.value = false
}

function apply() {
    if (!saving.value) configure({ mcpBaseUrl: url.value.trim(), externalMcpEnabled: enabled.value })
}

async function toggle(event) {
    const checked = event.target.checked
    await configure({ mcpBaseUrl: store.config.mcpBaseUrl || '', externalMcpEnabled: checked })
    event.target.checked = enabled.value
}

async function copyEndpoint() {
    try { await navigator.clipboard.writeText(endpoint.value) }
    catch { store.error = 'Cannot copy the MCP URL. Select and copy it manually.' }
}

function manage() { window.dispatchEvent(new CustomEvent('twicc:open-mcp-manager')) }
</script>

<template>
    <section class="settings-section">
        <div class="mcp-help-heading">
            <h3 class="settings-section-title">External MCP</h3>
            <HelpFeatureLink help-key="external-mcp" label="What is external MCP?" />
        </div>

        <!-- Once the feature is usable this is the daily action, and the
             fields below become set-once configuration — so it leads, like
             the Peers section's manager button. -->
        <template v-if="hasActions">
            <div class="setting-group">
                <wa-button size="small" variant="neutral" appearance="accent" @click="manage">
                    <wa-icon name="plug" slot="start"></wa-icon>
                    <span class="mcp-action-label">
                        Manage connections
                        <wa-badge v-if="store.pendingRequests.length" variant="brand">{{ store.pendingRequests.length }}</wa-badge>
                    </span>
                </wa-button>
            </div>
            <wa-divider></wa-divider>
        </template>

        <span class="setting-group-hint">
            Connect external MCP clients to this TwiCC instance. Internal agents keep automatic access.
        </span>

        <div class="setting-group">
            <label class="setting-group-label">Dedicated MCP URL <wa-icon name="cloud" class="synced-icon"></wa-icon></label>
            <div class="setting-input-apply-row">
                <wa-input
                    :value="url" @input="url = $event.target.value" @keydown.enter.prevent="apply"
                    placeholder="https://mcp.example.com" size="small" aria-label="Dedicated MCP URL"
                ></wa-input>
                <wa-button size="small" variant="neutral" :disabled="saving" @click="apply">
                    <wa-icon :name="applied ? 'check' : 'triangle-exclamation'" slot="start"></wa-icon>
                    Apply
                </wa-button>
            </div>
            <span class="setting-group-hint">
                Your address for external MCP clients. Enter the HTTPS origin only, without <code>/mcp</code>.
                Use a dedicated hostname, different from your External, Sharing and Peers addresses.
                This hostname serves only MCP and its OAuth endpoints; the TwiCC interface stays inaccessible there.
                The host must be reachable machine-to-machine: a tunnel-level access gate
                (e.g. Cloudflare Access asking for an email or Google account) blocks MCP connections.
                Use a public hostname without that gate. TwiCC protects MCP access with OAuth and requires
                your approval for each connection.
            </span>
        </div>

        <div class="setting-group">
            <label class="setting-group-label">External access <wa-icon name="cloud" class="synced-icon"></wa-icon></label>
            <wa-switch
                size="small" :checked="enabled"
                :disabled="saving || !passwordConfigured || !store.config.mcpBaseUrl" @change="toggle"
            >Accept external MCP clients</wa-switch>
            <wa-callout v-if="store.config.passwordConfigured === false" variant="danger" size="small">
                External MCP requires a TwiCC password. Set a password and restart TwiCC before enabling external access.
            </wa-callout>
            <span class="setting-group-hint">
                Needs the URL above. Changing that URL or turning access off revokes existing connections.
            </span>
        </div>

        <McpProtection />

        <wa-callout v-if="store.error || store.loadError" variant="danger" size="small">
            {{ store.error || store.loadError }}
        </wa-callout>

        <div v-if="enabled" class="setting-group">
            <label class="setting-group-label">MCP endpoint</label>
            <div class="mcp-endpoint-row">
                <code class="mcp-endpoint">{{ endpoint }}</code>
                <wa-button size="small" variant="neutral" appearance="outlined" @click="copyEndpoint">
                    <wa-icon name="copy" slot="start"></wa-icon>
                    Copy
                </wa-button>
            </div>
            <span class="setting-group-hint">Give this URL to the external MCP client.</span>
        </div>
    </section>
</template>

<style scoped>
/* Title plus help link, read as one heading — the Peers section's shape. */
.mcp-help-heading {
    display: flex;
    flex-direction: column;
    align-items: flex-start;
    gap: var(--wa-space-3xs);
}

/* wa-button pins any wa-badge slotted straight into it to its top corner —
   the wrapper takes it out of that rule, like the Peers action buttons. */
.mcp-action-label {
    display: inline-flex;
    align-items: center;
    gap: var(--wa-space-xs);
}
.mcp-endpoint-row {
    display: flex;
    align-items: center;
    flex-wrap: wrap;
    gap: var(--wa-space-xs);
}
.mcp-endpoint {
    user-select: all;
    overflow-wrap: anywhere;
    min-width: 0;
}

code { overflow-wrap: anywhere; }
</style>
