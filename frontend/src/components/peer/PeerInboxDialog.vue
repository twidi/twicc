<script setup>
/**
 * PeerInboxDialog — the peer inbox: pending requests, pending inbound
 * messages, recent history. A missed toast must never lose a message — this
 * panel (plus the badge button) is the persistent surface. Every message row
 * is clickable, history included: a resolved message reopens read-only, and a
 * delivered one can be routed again from there.
 *
 * Mounted once in App.vue, opened by `twicc:open-peer-inbox` (optionally with
 * `detail.messageId` → App.vue opens the review dialog directly).
 */
import { ref, computed, watch, onBeforeUnmount } from 'vue'
import { usePeersStore } from '../../stores/peers'
import { apiFetch } from '../../utils/api'
import { debounce } from '../../utils/debounce'
import {
    buildPeerInboxSearchUrl,
    peerInboxFiltersActive,
    peerInboxSelectablePeers,
    peerInboxVisibleMessages,
    peerInboxView,
} from '../../utils/peerInboxFilter'
import PeerInboxRow from './PeerInboxRow.vue'
import PeerHelpLink from './PeerHelpLink.vue'

const props = defineProps({ open: Boolean })
const emit = defineEmits(['close', 'review'])

const peersStore = usePeersStore()
const dialogRef = ref(null)
const selectedPeerId = ref('')
const textFilter = ref('')
const filteredMessages = ref([])
const searching = ref(false)
const searchError = ref('')
const historyHasMore = ref(false)
let searchGeneration = 0
let searchController = null

const pendingRequests = computed(() => peersStore.pendingRequests)
const selectablePeers = computed(() =>
    peerInboxSelectablePeers(peersStore.peers, peersStore.messages)
)
const filtersActive = computed(() =>
    peerInboxFiltersActive(selectedPeerId.value, textFilter.value)
)
const activeMessages = computed(() => {
    if (filtersActive.value) return filteredMessages.value
    return peerInboxVisibleMessages(peersStore.messages, peersStore.peers)
})
const inboxView = computed(() => peerInboxView(activeMessages.value, filtersActive.value))
const receivedMessages = computed(() => inboxView.value.received)
const history = computed(() => inboxView.value.history)
const emptyMessage = computed(() => inboxView.value.emptyMessage)

function invalidateSearch() {
    searchGeneration += 1
    searchController?.abort()
    searchController = null
}

async function searchMessages(generation) {
    if (!props.open || !filtersActive.value || generation !== searchGeneration) return
    const controller = new AbortController()
    searchController = controller
    try {
        const response = await apiFetch(
            buildPeerInboxSearchUrl(selectedPeerId.value, textFilter.value),
            { signal: controller.signal },
        )
        if (!response.ok) throw new Error(`Search failed with status ${response.status}`)
        const payload = await response.json()
        if (generation !== searchGeneration || controller.signal.aborted) return
        filteredMessages.value = payload.messages || []
        historyHasMore.value = !!payload.history_has_more
    } catch (error) {
        if (controller.signal.aborted || generation !== searchGeneration) return
        searchError.value = 'Could not search peer messages.'
    } finally {
        if (generation === searchGeneration) {
            searching.value = false
            searchController = null
        }
    }
}

const debouncedSearch = debounce(() => searchMessages(searchGeneration), 300)

function prepareSearch(immediate) {
    debouncedSearch.cancel()
    invalidateSearch()
    filteredMessages.value = []
    historyHasMore.value = false
    searchError.value = ''
    if (!props.open || !filtersActive.value) {
        searching.value = false
        return
    }
    searching.value = true
    if (immediate) searchMessages(searchGeneration)
    else debouncedSearch()
}

watch(selectedPeerId, () => prepareSearch(true))
watch(textFilter, () => prepareSearch(false))
watch(() => props.open, (open) => {
    if (open) {
        if (filtersActive.value) prepareSearch(true)
        return
    }
    debouncedSearch.cancel()
    invalidateSearch()
    searching.value = false
})
watch(
    () => peersStore.messages.map(message =>
        `${message.id}:${message.status}:${message.title}:${message.text_preview}`
    ).join('|'),
    () => {
        if (props.open && filtersActive.value) prepareSearch(true)
    },
)

onBeforeUnmount(() => {
    debouncedSearch.cancel()
    invalidateSearch()
})

function openManager() {
    emit('close')
    window.dispatchEvent(new CustomEvent('twicc:open-peers-manager'))
}

const hasActivePeer = computed(() => peersStore.peers.some(p => p.state === 'active'))

function composeNew() {
    emit('close')
    window.dispatchEvent(new CustomEvent('twicc:open-peer-compose', {
        // The peer filter, when set, is the obvious default recipient.
        detail: { peerId: selectedPeerId.value || null, returnTo: 'inbox' },
    }))
}

function review(message) {
    emit('review', message.id)
}

function onHide(event) {
    if (event.target !== dialogRef.value) return
    emit('close')
}
</script>

<template>
    <wa-dialog
        ref="dialogRef" :open="open" label="Peer inbox"
        style="--width: min(680px, calc(100vw - 2rem))"
        @wa-hide="onHide"
    >
        <div slot="label" class="peer-dialog-title">
            <span>Peer inbox</span>
            <PeerHelpLink />
        </div>

        <div class="pi-filters">
            <wa-select v-model="selectedPeerId" size="small" class="pi-filter-peer">
                <wa-option value="">All peers</wa-option>
                <wa-option
                    v-for="peer in selectablePeers" :key="peer.id"
                    :value="peer.id" :label="peersStore.peerLabel(peer.id)"
                >{{ peersStore.peerLabel(peer.id) }}</wa-option>
            </wa-select>
            <wa-input
                size="small" class="pi-filter-text" placeholder="Filter messages…"
                with-clear
                :value="textFilter"
                @input="textFilter = $event.target.value"
            ></wa-input>
        </div>

        <div v-if="searching" class="pi-searching" role="status" aria-live="polite">
            <wa-spinner></wa-spinner>
            <span>Searching…</span>
        </div>
        <wa-callout v-else-if="searchError" variant="danger" size="small">
            {{ searchError }}
        </wa-callout>

        <template v-if="pendingRequests.length">
            <h4 class="pi-section-title">Pending requests</h4>
            <button
                v-for="peer in pendingRequests" :key="peer.id"
                type="button" class="pi-row pi-row--clickable"
                @click="openManager"
            >
                <wa-icon name="user-plus" class="pi-row__icon"></wa-icon>
                <span class="pi-row__title">{{ peer.remote_display_name || 'unnamed instance' }}</span>
                <span class="pi-row__meta">{{ peer.base_url }}</span>
                <span class="pi-row__hint">Review in manager</span>
            </button>
        </template>

        <template v-if="!searching && !searchError">
            <template v-if="receivedMessages.length">
                <h4 class="pi-section-title">Received messages awaiting review</h4>
                <PeerInboxRow
                    v-for="message in receivedMessages" :key="message.id"
                    :message="message" :show-status="false"
                    @click="review(message)"
                />
            </template>

            <template v-if="history.length">
                <h4 class="pi-section-title">History</h4>
                <!-- Re-openable: a resolved message stays readable, and a delivered
                     one can be routed again (wrong session picked, draft cleared). -->
                <PeerInboxRow
                    v-for="message in history" :key="message.id"
                    :message="message"
                    @click="review(message)"
                />
                <wa-callout v-if="historyHasMore" variant="neutral" size="small">
                    Showing the first 200 results. Refine your filters to narrow the search.
                </wa-callout>
            </template>

            <p v-if="emptyMessage" class="pi-empty">{{ emptyMessage }}</p>
        </template>

        <div slot="footer" class="pi-footer">
            <wa-button v-if="hasActivePeer" appearance="outlined" @click="composeNew">
                <wa-icon name="paper-plane" slot="start"></wa-icon>
                New message
            </wa-button>
            <wa-button appearance="outlined" @click="openManager">
                <wa-icon name="user-group" slot="start"></wa-icon>
                Manage peers
            </wa-button>
            <wa-button @click="emit('close')">Close</wa-button>
        </div>
    </wa-dialog>
</template>

<style scoped>
.peer-dialog-title {
    display: flex;
    flex-direction: column;
    align-items: flex-start;
    gap: var(--wa-space-3xs);
}
.pi-filters {
    display: flex;
    align-items: center;
    gap: var(--wa-space-xs);
    margin-bottom: var(--wa-space-s);
}
.pi-filter-peer { flex: 0 1 40%; min-width: 0; }
.pi-filter-text { flex: 1 1 auto; min-width: 0; }
.pi-searching {
    display: flex;
    align-items: center;
    gap: var(--wa-space-xs);
    min-height: 2.5rem;
    color: var(--wa-color-text-quiet);
}
.pi-searching wa-spinner { font-size: 1rem; }
/* Callouts sitting directly in the dialog body own their separation from the
   blocks around them, whatever renders next to them (see PeersManagerDialog). */
wa-dialog > wa-callout { margin-block: var(--wa-space-s); }
.pi-section-title {
    margin: var(--wa-space-l) 0 var(--wa-space-s);
    border-bottom: 2px solid var(--wa-color-surface-border);
    padding-bottom: 0.35rem;
}
.pi-section-title:first-of-type { margin-top: 0; }
.pi-empty { color: var(--wa-color-text-quiet); }

.pi-row {
    display: flex;
    align-items: center;
    gap: var(--wa-space-s);
    width: 100%;
    padding: var(--wa-space-xs) 0;
    border-bottom: 1px solid var(--wa-color-surface-border);
    min-width: 0;
    background: none;
    border-left: none;
    border-right: none;
    border-top: none;
    color: inherit;
    font: inherit;
    text-align: left;
}
.pi-row:last-of-type { border-bottom: none; }
.pi-row--clickable { cursor: pointer; }
.pi-row--clickable:hover { background: var(--wa-color-surface-raised); }

.pi-row__icon { color: var(--wa-color-text-quiet); flex-shrink: 0; }
.pi-row__title { font-weight: 600; flex-shrink: 0; }
.pi-row__meta {
    color: var(--wa-color-text-quiet);
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
    min-width: 0;
}
.pi-row__hint { margin-left: auto; color: var(--wa-color-text-quiet); font-size: 0.8rem; flex-shrink: 0; }

.pi-footer {
    display: flex;
    justify-content: flex-end;
    gap: var(--wa-space-s);
    width: 100%;
}

@media (max-width: 520px) {
    .pi-filters { align-items: stretch; flex-direction: column; }
    .pi-filter-peer,
    .pi-filter-text { width: 100%; }
}
</style>
