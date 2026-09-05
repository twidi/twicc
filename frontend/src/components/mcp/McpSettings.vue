<script setup>
import { ref, onMounted } from 'vue'
import { useMcpStore } from '../../stores/mcp'
const store = useMcpStore()
const url = ref('')
const enabled = ref(false)
const saving = ref(false)
onMounted(async () => {
    await store.refresh()
    url.value = store.config.mcpBaseUrl || ''
    enabled.value = store.config.externalMcpEnabled === true
})
async function save() {
    saving.value = true
    await store.act('configure', { mcpBaseUrl: url.value.trim(), externalMcpEnabled: enabled.value })
    saving.value = false
}
async function copyEndpoint() {
    try { await navigator.clipboard.writeText(`${store.config.mcpBaseUrl}/mcp`) }
    catch { store.error = 'Cannot copy the MCP URL. Select and copy it manually.' }
}
function manage() { window.dispatchEvent(new CustomEvent('twicc:open-mcp-manager')) }
</script>
<template>
    <section class="mcp-settings">
        <h3>MCP</h3>
        <p>Connect external MCP clients to this TwiCC instance. Internal agents keep automatic access.</p>
        <form @submit.prevent="save">
            <label><input type="checkbox" v-model="enabled"> Enable external MCP access</label>
            <label>Dedicated MCP URL
                <input v-model="url" type="url" placeholder="https://mcp.example.com" :required="enabled">
            </label>
            <p>Use a separate HTTPS hostname. The tunnel must not require a provider login.</p>
            <p>Changing this URL or disabling access revokes existing connections.</p>
            <button type="submit" :disabled="saving">{{ saving ? 'Saving…' : 'Save' }}</button>
        </form>
        <p v-if="store.config.externalMcpEnabled"><strong>MCP endpoint:</strong> <code>{{ store.config.mcpBaseUrl }}/mcp</code></p>
        <wa-button v-if="store.config.externalMcpEnabled" @click="copyEndpoint">Copy MCP URL</wa-button>
        <wa-button @click="manage">Manage connections <span v-if="store.requests.length">({{ store.requests.length }} pending)</span></wa-button>
        <wa-callout v-if="store.error || store.loadError" variant="danger">{{ store.error || store.loadError }}</wa-callout>
    </section>
</template>
<style scoped>
.mcp-settings{display:grid;gap:1rem}.mcp-settings form{display:grid;gap:.8rem}
label{display:block}input[type=url]{display:block;box-sizing:border-box;width:100%;padding:.6rem;margin-top:.4rem}
p{line-height:1.5;margin:0}button{padding:.5rem 1rem;width:fit-content}code{overflow-wrap:anywhere}
</style>
