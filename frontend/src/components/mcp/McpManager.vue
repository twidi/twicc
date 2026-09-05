<script setup>
/**
 * McpManager - external MCP connections and pending approval requests.
 *
 * Mounted once in App.vue, opened by the `twicc:open-mcp-manager` CustomEvent
 * (MCP settings section, approval toasts). Its detail is a nested dialog,
 * McpConnectionDialog, keyed per row.
 *
 * Layout follows PeersManagerDialog: own address on top, one rule-headed
 * section per group, stacked rows with plain actions and inline confirmations.
 *
 * A pending request has no display timeout (design §7). The toast per request
 * lives here so it can be cleared on every device the moment the request
 * leaves `store.pendingRequests` — approved, refused or expired.
 *
 * `client_name` comes from an unauthenticated OAuth client registration —
 * text interpolation ONLY, never v-html.
 */
import { ref, computed, watch, onMounted, onBeforeUnmount, defineAsyncComponent } from 'vue'
import { useMcpStore } from '../../stores/mcp'
import { useAuthStore } from '../../stores/auth'
import { useSettingsStore } from '../../stores/settings'
import { SESSION_TIME_FORMAT } from '../../constants'
import { formatDate } from '../../utils/date'
import { toast } from '../../composables/useToast'
import McpConnectionDialog from './McpConnectionDialog.vue'
import HelpFeatureLink from '../help/HelpFeatureLink.vue'

const McpToast = defineAsyncComponent(() => import('./McpToast.vue'))

const store = useMcpStore()
const auth = useAuthStore()
const settings = useSettingsStore()

// Timestamps follow the global time-format setting, like every other list
// (PeersManagerDialog is the reference).
const useRelativeTime = computed(() =>
    [SESSION_TIME_FORMAT.RELATIVE_SHORT, SESSION_TIME_FORMAT.RELATIVE_NARROW].includes(settings.getSessionTimeFormat)
)
const relativeTimeFormat = computed(() =>
    settings.getSessionTimeFormat === SESSION_TIME_FORMAT.RELATIVE_SHORT ? 'short' : 'narrow'
)
function absoluteTime(iso) {
    return formatDate(Math.floor(new Date(iso).getTime() / 1000), { smart: true })
}

const listOpen = ref(false)
const selected = ref(null)
const fromList = ref(false)
const busy = ref({})
const confirmingRevoke = ref(null)
const notifications = new Map()

const active = computed(() => store.connections.filter(connection => !connection.revoked))
const revoked = computed(() => store.connections.filter(connection => connection.revoked))
const groups = computed(() => [
    { title: 'Pending requests', kind: 'request', rows: store.pendingRequests },
    { title: 'Active', kind: 'connection', rows: active.value },
    { title: 'Revoked', kind: 'connection', rows: revoked.value },
].filter(group => group.rows.length))
const current = computed(() => (selected.value?.kind === 'request'
    ? store.pendingRequests.find(row => row.id === selected.value.id)
    : store.connections.find(row => row.id === selected.value?.id)))

// Only once the config has actually loaded — an empty store must not flash a
// "not configured" warning while the first GET is in flight.
const configLoaded = computed(() => 'mcpBaseUrl' in store.config)
const disabledReason = computed(() => {
    if (!configLoaded.value) return ''
    if (!store.config.mcpBaseUrl) return 'External MCP is disabled — set your MCP address in Settings › External MCP first.'
    if (!store.config.externalMcpEnabled) return 'External MCP is turned off — enable it in Settings › External MCP to accept new connections.'
    return ''
})

function tagVariant(group, entry) {
    if (group.kind === 'request') return 'warning'
    if (entry.revoked) return 'danger'
    return entry.active ? 'success' : 'warning'
}
function tagLabel(group, entry) {
    if (group.kind === 'request') return 'pending'
    if (entry.revoked) return 'revoked'
    return entry.active ? 'active' : 'connecting'
}

// A pending request lapses on a deadline nobody broadcasts (see the store's
// `pendingRequests`). One timer, re-armed on the nearest deadline, retires the
// row and its toast on time — no endpoint polling.
let expiryTimer
function armExpiryTimer() {
    clearTimeout(expiryTimer)
    const deadlines = store.pendingRequests
        .map(row => new Date(row.expires_at).getTime())
        .filter(time => Number.isFinite(time))
    if (!deadlines.length) return
    // A small margin absorbs clock skew, so the timer never fires a tick early
    // and re-arms on the same deadline.
    expiryTimer = setTimeout(() => {
        store.clock = Date.now()
        armExpiryTimer()
    }, Math.max(Math.min(...deadlines) - Date.now(), 0) + 250)
}

async function refresh() {
    if (!auth.checking && !auth.needsLogin) await store.refresh()
}

async function open(event) {
    await refresh()
    const id = event?.detail?.requestId
    if (id && store.pendingRequests.some(row => row.id === id)) {
        select('request', id, false)
        return
    }
    // No request to review (or it is already resolved) — the list is still
    // what the user asked for.
    selected.value = null
    listOpen.value = true
}

function select(kind, id, returnToList = true) {
    store.error = ''
    confirmingRevoke.value = null
    fromList.value = returnToList
    listOpen.value = false
    selected.value = { kind, id }
}

function closeDetail() {
    if (!selected.value) return
    selected.value = null
    listOpen.value = fromList.value
    fromList.value = false
}

async function revoke(id) {
    busy.value = { ...busy.value, [id]: true }
    await store.act('revoke', { id })
    busy.value = { ...busy.value, [id]: false }
    confirmingRevoke.value = null
}

watch(() => store.pendingRequests, requests => {
    const ids = new Set(requests.map(row => row.id))
    for (const [id, notification] of notifications) {
        if (!ids.has(id)) { notification.clear(); notifications.delete(id) }
    }
    for (const request of requests) {
        if (!notifications.has(request.id)) notifications.set(request.id, toast.custom(McpToast, {
            title: 'MCP connection request', duration: Infinity, persistent: true,
            props: { requestId: request.id, clientName: request.client_name },
        }))
    }
    if (selected.value?.kind === 'request' && !ids.has(selected.value.id)) closeDetail()
    armExpiryTimer()
})

onMounted(() => {
    window.addEventListener('twicc:open-mcp-manager', open)
    window.addEventListener('twicc:mcp-updated', refresh)
    window.addEventListener('focus', refresh)
    refresh()
})
onBeforeUnmount(() => {
    clearTimeout(expiryTimer)
    for (const notification of notifications.values()) notification.clear()
    window.removeEventListener('twicc:open-mcp-manager', open)
    window.removeEventListener('twicc:mcp-updated', refresh)
    window.removeEventListener('focus', refresh)
})
</script>

<template>
    <wa-dialog
        :open="listOpen" label="MCP connections"
        style="--width: min(760px, calc(100vw - 2rem))"
        @wa-hide.self="listOpen = false"
    >
        <div slot="label" class="mcp-dialog-title">
            <span>MCP connections</span>
            <HelpFeatureLink help-key="external-mcp" label="What is external MCP?" />
        </div>

        <wa-callout v-if="disabledReason" variant="warning" size="small" class="mcp-block">
            {{ disabledReason }}
        </wa-callout>
        <p v-else-if="store.config.mcpBaseUrl" class="mcp-own-address">
            Your MCP address: <code>{{ store.config.mcpBaseUrl }}/mcp</code>
        </p>

        <wa-callout v-if="store.error || store.loadError" variant="danger" size="small">
            {{ store.error || store.loadError }}
        </wa-callout>

        <template v-for="group in groups" :key="group.title">
            <h4 class="mcp-section-title">{{ group.title }}</h4>
            <div v-for="entry in group.rows" :key="entry.id" class="mcp-connection">
                <div class="mcp-connection__main">
                    <span class="mcp-connection__name">
                        {{ entry.name || entry.client_name || 'External MCP client' }}
                    </span>
                    <wa-tag :variant="tagVariant(group, entry)" size="small">{{ tagLabel(group, entry) }}</wa-tag>
                    <span
                        class="mcp-connection__time"
                        :title="group.kind === 'request' || !entry.last_used_at ? 'Created' : 'Last used'"
                    >
                        <wa-relative-time
                            v-if="useRelativeTime"
                            :date.prop="new Date(entry.last_used_at || entry.created_at)"
                            :format="relativeTimeFormat" numeric="always" sync
                        ></wa-relative-time>
                        <template v-else>{{ absoluteTime(entry.last_used_at || entry.created_at) }}</template>
                    </span>
                </div>
                <div class="mcp-connection__client">
                    {{ group.kind === 'request' ? 'Awaiting your approval' : entry.client_name || 'External MCP client' }}
                </div>
                <div class="mcp-connection__actions">
                    <wa-button
                        size="small" appearance="plain"
                        :variant="group.kind === 'request' ? 'brand' : 'neutral'"
                        @click="select(group.kind, entry.id)"
                    >{{ group.kind === 'request' ? 'Review' : 'Details' }}</wa-button>
                    <wa-button
                        v-if="group.kind === 'connection' && !entry.revoked"
                        size="small" variant="danger" appearance="plain"
                        :disabled="busy[entry.id]"
                        @click="confirmingRevoke = entry.id"
                    >Revoke</wa-button>
                </div>
                <wa-callout v-if="confirmingRevoke === entry.id" variant="warning" size="small">
                    <div class="mcp-confirm-body">
                        <span>
                            Revokes this connection immediately and rejects its next request.
                            It does not cancel an operation already running.
                        </span>
                        <span class="mcp-confirm__actions">
                            <wa-button size="small" variant="danger" :disabled="busy[entry.id]" @click="revoke(entry.id)">
                                Revoke
                            </wa-button>
                            <wa-button size="small" appearance="outlined" @click="confirmingRevoke = null">Keep</wa-button>
                        </span>
                    </div>
                </wa-callout>
            </div>
        </template>

        <p v-if="!groups.length" class="mcp-empty">No MCP connections yet.</p>

        <wa-button slot="footer" @click="listOpen = false">Close</wa-button>
    </wa-dialog>

    <McpConnectionDialog
        v-if="selected && current"
        :key="`${selected.kind}:${selected.id}`"
        :entry="current" :review="selected.kind === 'request'"
        @close="closeDetail"
    />
</template>

<style scoped>
/* Mirrors PeersManagerDialog: address, section rules, stacked rows, plain
   actions. Callouts sitting directly in the dialog body are block-flow
   siblings, so the separation belongs to the callout, not to whatever block
   happens to precede it. */
wa-dialog > wa-callout { margin-block: var(--wa-space-s); }

.mcp-dialog-title {
    display: flex;
    flex-direction: column;
    align-items: flex-start;
    gap: var(--wa-space-3xs);
}

.mcp-block { margin-bottom: var(--wa-space-m); }
.mcp-own-address {
    margin: 0 0 var(--wa-space-m);
    color: var(--wa-color-text-quiet);
}
.mcp-own-address code { user-select: all; overflow-wrap: anywhere; }

.mcp-section-title {
    margin: var(--wa-space-l) 0 var(--wa-space-s);
    border-bottom: 2px solid var(--wa-color-surface-border);
    padding-bottom: 0.35rem;
}
.mcp-section-title:first-of-type { margin-top: 0; }
.mcp-empty { color: var(--wa-color-text-quiet); }

.mcp-connection {
    display: flex;
    flex-direction: column;
    gap: var(--wa-space-xs);
    padding: var(--wa-space-s) 0;
    border-bottom: 1px solid var(--wa-color-surface-border);
}
.mcp-connection:last-of-type { border-bottom: none; }

/* Unlike a peer row this one wraps: a client-declared name has no length
   limit, and the tag must stay next to it rather than be pushed out. */
.mcp-connection__main {
    display: flex;
    align-items: center;
    flex-wrap: wrap;
    gap: var(--wa-space-s);
    min-width: 0;
}
.mcp-connection__name { font-weight: 600; overflow-wrap: anywhere; min-width: 0; }
.mcp-connection__time {
    margin-left: auto;
    flex: none;
    color: var(--wa-color-text-quiet);
    font-size: 0.8rem;
}
/* The identity the client declares, under the name the owner chose. */
.mcp-connection__client {
    color: var(--wa-color-text-quiet);
    font-size: 0.85rem;
    overflow-wrap: anywhere;
}
.mcp-connection__actions {
    display: flex;
    align-items: center;
    flex-wrap: wrap;
    gap: var(--wa-space-xs);
}

/* Confirmation callouts: breathing room between the text and the buttons. */
.mcp-confirm-body {
    display: flex;
    flex-direction: column;
    gap: var(--wa-space-s);
}
.mcp-confirm__actions {
    display: flex;
    flex-wrap: wrap;
    gap: var(--wa-space-s);
}
</style>
