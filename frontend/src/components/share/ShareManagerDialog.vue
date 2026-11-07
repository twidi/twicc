<script setup>
import { useI18n } from 'vue-i18n'
const { t } = useI18n()
// The global "Shared links" screen. Rather than one flat list (where an
// unlabelled link gives no clue what it points at), links are grouped by the
// object they share — one session or one artifact — each group headed by its
// kind + title, then that object's links via the same ShareListPanel used by the
// per-target dialog (reused once per shared object instead of once for all).
import { ref, computed } from 'vue'
import { useSharesStore } from '../../stores/shares'
import { ARTIFACT_ICON } from '../../utils/artifactBookmark'
import ShareListPanel from './ShareListPanel.vue'
import ShareDialog from './ShareDialog.vue'

defineProps({ open: Boolean })
const emit = defineEmits(['close'])
const store = useSharesStore()
const editing = ref(null)
const dialogRef = ref(null)

// Group the (already newest-first) flat list by shared object. Groups keep that
// order — a group appears where its newest link falls — and each group's links
// stay newest-first. Key is kind + object id so a session and an artifact can't
// collide on a shared numeric id.
const groups = computed(() => {
    const map = new Map()
    for (const s of store.list) {
        const isArtifact = s.kind === 'artifact'
        const objId = isArtifact ? s.bookmark_id : s.session_id
        const key = `${s.kind}:${objId}`
        let g = map.get(key)
        if (!g) {
            g = {
                key,
                kind: s.kind,
                icon: isArtifact ? ARTIFACT_ICON : 'comment',
                kindLabel: isArtifact ? t('sharing.links.artifactKind') : t('sharing.links.sessionKind'),
                title: (isArtifact ? s.target_name : s.target_title) || '',
                shares: [],
            }
            map.set(key, g)
        }
        g.shares.push(s)
    }
    return [...map.values()]
})

function onHide(e) { if (e.target === dialogRef.value) emit('close') }
</script>

<template>
    <wa-dialog ref="dialogRef" :open="open" :label="$t('sharing.links.title')"
               style="--width: min(720px, calc(100vw - 2rem))" @wa-hide="onHide">
        <p v-if="!groups.length" class="sm-empty muted">{{ $t('sharing.links.empty') }}</p>
        <div v-for="g in groups" :key="g.key" class="sm-group">
            <div class="sm-group__header">
                <wa-icon :name="g.icon" class="sm-group__icon"></wa-icon>
                <span class="sm-group__kind">{{ g.kindLabel }}</span>
                <span class="sm-group__title">{{ g.title || (g.kind === 'artifact' ? $t('sharing.links.unnamedArtifact') : $t('sharing.links.untitledSession')) }}</span>
                <span class="sm-group__count">{{ $t('sharing.links.count', { count: g.shares.length }, g.shares.length) }}</span>
            </div>
            <ShareListPanel :shares="g.shares" @edit="editing = $event" />
        </div>
        <ShareDialog v-if="editing" :open="!!editing" :kind="editing.kind"
                     :session-id="editing.session_id" :bookmark-id="editing.bookmark_id"
                     :allowed-hosts="editing.allowed_hosts || {}"
                     :default-title="editing.target_title || editing.target_name || ''"
                     :edit="editing" @close="editing = null" />
        <div slot="footer" class="dialog-footer"><wa-button @click="emit('close')">{{ $t('common.actions.close') }}</wa-button></div>
    </wa-dialog>
</template>

<style scoped>
.sm-empty { color: var(--wa-color-text-quiet); }
.sm-group { margin-bottom: 1.25rem; }
.sm-group:last-of-type { margin-bottom: 0; }
.sm-group__header {
    display: flex;
    align-items: center;
    gap: 0.5rem;
    padding-bottom: 0.35rem;
    border-bottom: 2px solid var(--wa-color-surface-border);
}
.sm-group__icon { color: var(--wa-color-text-quiet); flex-shrink: 0; }
.sm-group__kind {
    font-size: 0.68rem;
    text-transform: uppercase;
    letter-spacing: 0.05em;
    font-weight: 700;
    color: var(--wa-color-text-quiet);
    flex-shrink: 0;
}
.sm-group__title {
    font-weight: 600;
    flex: 1;
    min-width: 0;
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
}
.sm-group__count { font-size: 0.8rem; color: var(--wa-color-text-quiet); white-space: nowrap; flex-shrink: 0; }
/* Indent each object's links under its group header (this dialog only — the
   per-target dialog reuses ShareListPanel flush). */
.sm-group :deep(.share-list) { margin-left: 1rem; }
.dialog-footer {
    display: flex;
    gap: var(--wa-space-s);
    justify-content: flex-end;
    width: 100%;
    align-items: center;
    flex-wrap: wrap;
}
</style>
