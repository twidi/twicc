<script setup>
import { ref, computed, useId } from 'vue'
import { useRoute } from 'vue-router'
import { useSharesStore } from '../../stores/shares'
import { shareAbsoluteUrl } from '../../utils/shareUrl'
import { shareCreatorBadge } from '../../utils/shareCreatorBadge'
import { isShareOutdated } from '../../utils/shareStatus'
import { toast } from '../../composables/useToast'
import AppTooltip from '../ui/AppTooltip.vue'
import AccessLogList from './AccessLogList.vue'

const props = defineProps({ shares: { type: Array, required: true } })
const emit = defineEmits(['edit'])
const store = useSharesStore()

// Per-instance prefix so row-action tooltip ids stay unique even if two panels
// (the global manager and a per-target dialog) render the same share at once.
const uid = useId()

// This panel always renders one object's links, so a bulk "revoke all active"
// belongs here — it surfaces in both the per-target dialog and each group of the
// global manager. Only offered past a single active link (one link → its own row
// button is enough).
const activeShares = computed(() => props.shares.filter((s) => s.status === 'active'))
const revokingAll = ref(false)
const revokeAllConfirm = ref(false) // inline "Revoke all?" confirmation pending
async function revokeAll() {
    const active = activeShares.value
    if (active.length < 1 || revokingAll.value) return
    revokingAll.value = true
    try {
        await Promise.all(active.map((s) => store.revokeShare(s.id, true)))
        toast.success('Active links revoked')
    } finally {
        revokingAll.value = false
        revokeAllConfirm.value = false
    }
}

// Per-share expanded "Recent views" panel: id -> accesses[] (null = loading).
const accesses = ref({})
const route = useRoute()
function creatorBadge(share) {
    return shareCreatorBadge(share.created_by, route)
}
function copy(s) {
    const url = shareAbsoluteUrl(s)
    if (!url) { toast.error?.('Configure a share host in Settings → Sharing first.'); return }
    navigator.clipboard.writeText(url); toast.success('Share URL copied')
}
// Inline delete confirmation (same "Delete?" check/cross pattern as the workspace manager).
const deleteConfirmId = ref(null) // share ID pending delete confirmation
const deleting = ref(false)
async function confirmDelete(s) {
    if (deleting.value) return
    deleting.value = true
    try {
        await store.deleteShare(s.id)
    } finally {
        deleting.value = false
        deleteConfirmId.value = null
    }
}
async function toggleViews(s) {
    if (s.id in accesses.value) { delete accesses.value[s.id]; return }
    accesses.value[s.id] = null
    accesses.value[s.id] = await store.fetchAccesses(s.id)
}
</script>

<template>
    <div class="share-list">
        <div v-if="activeShares.length >= 2" class="share-list-toolbar">
            <template v-if="revokeAllConfirm">
                <span class="revoke-confirm-label">{{ $t('sharing.links.revokeAllQuestion', { count: activeShares.length }) }}</span>
                <wa-button :id="`${uid}-revoke-all-ok`" size="small" appearance="plain" variant="warning" :loading="revokingAll" @click="revokeAll"><wa-icon name="check"></wa-icon></wa-button>
                <AppTooltip :for="`${uid}-revoke-all-ok`">{{ $t('sharing.links.confirmRevokeAll') }}</AppTooltip>
                <wa-button :id="`${uid}-revoke-all-no`" size="small" appearance="plain" :disabled="revokingAll" @click="revokeAllConfirm = false"><wa-icon name="xmark"></wa-icon></wa-button>
                <AppTooltip :for="`${uid}-revoke-all-no`">{{ $t('sharing.links.cancelRevokeAll') }}</AppTooltip>
            </template>
            <wa-button v-else size="small" appearance="plain" variant="warning" @click="revokeAllConfirm = true">
                <wa-icon slot="start" name="ban"></wa-icon>{{ $t('sharing.links.revokeAll', { count: activeShares.length }) }}
            </wa-button>
        </div>
        <div v-for="s in shares" :key="s.id" class="share-row">
            <div class="share-row-main">
                <wa-tag size="small" :variant="s.status === 'active' ? 'success' : (s.status === 'expired' ? 'warning' : 'neutral')">
                    {{ s.status }}
                </wa-tag>
                <span class="share-label">{{ s.label || '(no label)' }}</span>
                <wa-tag v-if="creatorBadge(s)" size="small" variant="brand" class="share-agent-badge">
                    <wa-icon name="robot"></wa-icon>
                    <router-link v-if="creatorBadge(s).to" :to="creatorBadge(s).to">
                        {{ creatorBadge(s).label }}
                    </router-link>
                    <template v-else>{{ creatorBadge(s).label }}</template>
                </wa-tag>
                <wa-tag v-if="s.has_password" size="small" variant="neutral"><wa-icon name="lock"></wa-icon></wa-tag>
                <wa-tag v-if="isShareOutdated(s)" size="small" variant="warning">{{ $t('sharing.links.outdated') }}</wa-tag>
                <button class="share-views" type="button" @click="toggleViews(s)">{{ $t('sharing.links.views', { count: s.view_count }, s.view_count) }}</button>
            </div>
            <!-- Recent views expand right under their trigger, above the actions. -->
            <div v-if="s.id in accesses" class="share-views-panel">
                <p v-if="accesses[s.id] === null" class="muted">{{ $t('sharing.links.loading') }}</p>
                <p v-else-if="!accesses[s.id].length" class="muted">{{ $t('sharing.links.noViews') }}</p>
                <AccessLogList v-else :entries="accesses[s.id]" />
            </div>
            <div class="share-row-actions">
                <wa-button :id="`${uid}-copy-${s.id}`" size="small" appearance="plain" @click="copy(s)"><wa-icon name="copy"></wa-icon></wa-button>
                <AppTooltip :for="`${uid}-copy-${s.id}`">{{ $t('sharing.links.copyUrl') }}</AppTooltip>
                <wa-button v-if="isShareOutdated(s)" :id="`${uid}-push-${s.id}`" size="small" variant="warning" @click="store.propagateShare(s.id)">{{ $t('sharing.links.pushUpdate') }}</wa-button>
                <AppTooltip v-if="isShareOutdated(s)" :for="`${uid}-push-${s.id}`">{{ $t('sharing.links.pushUpdateHint') }}</AppTooltip>
                <wa-button :id="`${uid}-edit-${s.id}`" size="small" appearance="plain" @click="emit('edit', s)"><wa-icon name="pen"></wa-icon></wa-button>
                <AppTooltip :for="`${uid}-edit-${s.id}`">{{ $t('sharing.links.edit') }}</AppTooltip>
                <wa-button v-if="s.status !== 'revoked'" :id="`${uid}-revoke-${s.id}`" size="small" appearance="plain" @click="store.revokeShare(s.id, true)">{{ $t('sharing.links.revoke') }}</wa-button>
                <AppTooltip v-if="s.status !== 'revoked'" :for="`${uid}-revoke-${s.id}`">{{ $t('sharing.links.revokeHint') }}</AppTooltip>
                <wa-button v-if="s.status === 'revoked'" :id="`${uid}-unrevoke-${s.id}`" size="small" appearance="plain" @click="store.revokeShare(s.id, false)">{{ $t('sharing.links.unrevoke') }}</wa-button>
                <AppTooltip v-if="s.status === 'revoked'" :for="`${uid}-unrevoke-${s.id}`">{{ $t('sharing.links.unrevokeHint') }}</AppTooltip>
                <template v-if="deleteConfirmId === s.id">
                    <span class="delete-confirm-label">{{ $t('sharing.links.deleteQuestion') }}</span>
                    <wa-button :id="`${uid}-del-ok-${s.id}`" size="small" appearance="plain" variant="danger" :loading="deleting" @click="confirmDelete(s)"><wa-icon name="check"></wa-icon></wa-button>
                    <AppTooltip :for="`${uid}-del-ok-${s.id}`">{{ $t('sharing.links.confirmDelete') }}</AppTooltip>
                    <wa-button :id="`${uid}-del-no-${s.id}`" size="small" appearance="plain" :disabled="deleting" @click="deleteConfirmId = null"><wa-icon name="xmark"></wa-icon></wa-button>
                    <AppTooltip :for="`${uid}-del-no-${s.id}`">{{ $t('sharing.links.cancelDelete') }}</AppTooltip>
                </template>
                <template v-else>
                    <wa-button :id="`${uid}-del-${s.id}`" size="small" appearance="plain" variant="danger" @click="deleteConfirmId = s.id"><wa-icon name="trash"></wa-icon></wa-button>
                    <AppTooltip :for="`${uid}-del-${s.id}`">{{ $t('sharing.links.deleteHint') }}</AppTooltip>
                </template>
            </div>
        </div>
        <p v-if="!shares.length" class="share-empty muted">{{ $t('sharing.links.empty') }}</p>
    </div>
</template>

<style scoped>
.share-list-toolbar { display: flex; align-items: center; justify-content: flex-end; gap: 0.25rem; padding-bottom: 0.25rem; }
.revoke-confirm-label { font-size: 0.85rem; color: var(--wa-color-warning-60); }
.share-row { padding: 0.5rem 0; border-bottom: 1px solid var(--wa-color-surface-border); }
.share-row-main { display: flex; align-items: center; gap: 0.5rem; }
.share-label { flex: 1; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.share-agent-badge { flex-shrink: 0; }
.share-agent-badge a { color: inherit; }
.share-views { background: none; border: none; color: var(--wa-color-text-quiet); cursor: pointer; font-size: 0.85rem; text-decoration: underline dotted; }
.share-row-actions { display: flex; align-items: center; gap: 0.25rem; margin-top: 0.35rem; flex-wrap: wrap; }
.delete-confirm-label { font-size: 0.85rem; color: var(--wa-color-danger-60); white-space: nowrap; }
/* The fetched log is capped server-side (newest 200); AccessLogList itself
   caps the panel height so a busy link scrolls instead of blowing up the dialog. */
.share-views-panel { margin-top: 0.4rem; font-size: 0.8rem; }
.muted { color: var(--wa-color-text-quiet); }
</style>
