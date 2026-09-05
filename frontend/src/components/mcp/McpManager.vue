<script setup>
import { ref, watch, nextTick, onMounted, onBeforeUnmount, defineAsyncComponent } from 'vue'
import { useMcpStore } from '../../stores/mcp'
import { useAuthStore } from '../../stores/auth'
import { toast } from '../../composables/useToast'
const McpToast = defineAsyncComponent(() => import('./McpToast.vue'))
const store = useMcpStore()
const auth = useAuthStore()
const dialog = ref(null)
const review = ref(null)
const code = ref('')
const name = ref('')
const firstInput = ref(null)
const busy = ref(false)
const names = ref({})
let timer
let notification
let toastKey = ''
async function refresh() {
    if (!auth.checking && !auth.needsLogin) await store.refresh()
}
async function open() {
    await refresh()
    if (dialog.value) dialog.value.open = true
}
async function selectRequest(request) { review.value = request; store.error = ''; code.value = ''; name.value = ''; await nextTick(); focus() }
async function decide(action) {
    busy.value = true
    const ok = await store.act(action, { id: review.value.id, code: code.value.trim(), name: name.value.trim() })
    if (ok) review.value = null
    busy.value = false
}
async function rename(connection) {
    await store.act('rename', { id: connection.id, name: (names.value[connection.id] ?? connection.name).trim() })
}
function focus() { firstInput.value?.focus() }
watch(() => store.requests.map(r => r.id).sort().join(','), key => {
    if (key === toastKey) return
    notification?.clear?.()
    toastKey = key
    notification = key ? toast.custom(McpToast, { title: 'MCP connection request', duration: Infinity, persistent: true }) : null
    if (review.value && !store.requests.some(r => r.id === review.value.id)) review.value = null
})
onMounted(() => {
    window.addEventListener('twicc:open-mcp-manager', open)
    window.addEventListener('twicc:mcp-updated', refresh)
    window.addEventListener('focus', refresh)
    refresh()
    timer = setInterval(refresh, 5000)
})
onBeforeUnmount(() => {
    clearInterval(timer)
    notification?.clear?.()
    window.removeEventListener('twicc:open-mcp-manager', open)
    window.removeEventListener('twicc:mcp-updated', refresh)
    window.removeEventListener('focus', refresh)
})
</script>
<template>
    <wa-dialog ref="dialog" label="MCP connections" @wa-after-show.self="focus" style="--width:min(720px,calc(100vw - 2rem))">
        <div class="manager">
            <wa-callout v-if="store.error || store.loadError" variant="danger">{{ store.error || store.loadError }}</wa-callout>
            <h3>Pending requests</h3>
            <p v-if="!store.requests.length">No pending requests.</p>
            <article v-for="request in store.requests" :key="request.id">
                <strong>{{ request.client_name || 'External MCP client' }}</strong>
                <p class="identifier">{{ request.client_id }}</p>
                <wa-button size="small" @click="selectRequest(request)">Review</wa-button>
            </article>
            <form v-if="review" id="mcp-consent-form" @submit.prevent="decide('approve')">
                <h3>Authorize {{ review.client_name || 'external MCP client' }}</h3>
                <p><strong>Full access:</strong> this connection can read and change your TwiCC data, send messages, and manage sessions and shares.</p>
                <p class="identifier">Redirect: {{ review.redirect_uri }}</p>
                <p>Expires: {{ new Date(review.expires_at).toLocaleString() }}</p>
                <label>Verification code from the connecting browser
                    <input ref="firstInput" v-model="code" required maxlength="8" autocomplete="off">
                </label>
                <label>Connection name (optional)<input v-model="name" maxlength="80" placeholder="ChatGPT"></label>
                <div class="actions"><button type="submit" :disabled="busy">Authorize</button>
                    <button type="button" :disabled="busy" @click="decide('refuse')">Refuse</button></div>
            </form>
            <h3>Connections</h3>
            <p v-if="!store.connections.length">No connections.</p>
            <article v-for="connection in store.connections" :key="connection.id">
                <form @submit.prevent="rename(connection)">
                    <label>Connection name<input :value="names[connection.id] ?? connection.name" @input="names[connection.id] = $event.target.value" maxlength="80" placeholder="External MCP"></label>
                    <button type="submit">Save name</button>
                </form>
                <p>{{ connection.client_name || 'External MCP client' }}</p>
                <p class="identifier">{{ connection.client_id }}</p>
                <p>Created: {{ new Date(connection.created_at).toLocaleString() }}</p>
                <p>Last used: {{ connection.last_used_at ? new Date(connection.last_used_at).toLocaleString() : 'Not yet used' }}</p>
                <p v-if="!connection.revoked && !connection.active">Awaiting OAuth completion</p>
                <strong v-if="connection.revoked">Revoked</strong>
                <wa-button v-else size="small" variant="danger" @click="store.act('revoke', { id: connection.id })">Revoke</wa-button>
            </article>
        </div>
    </wa-dialog>
</template>
<style scoped>
.manager{display:grid;gap:1rem}h3,p{margin:0}p{line-height:1.5}
article,form{display:grid;gap:.6rem}article{padding:1rem;border:1px solid var(--wa-color-neutral-border-normal);border-radius:.5rem}
label{display:grid;gap:.3rem}input{padding:.5rem;box-sizing:border-box;width:100%;font:inherit}
button{width:fit-content;padding:.5rem 1rem}.actions{display:flex;gap:.7rem}.identifier{overflow-wrap:anywhere;font-size:.85em}
</style>
