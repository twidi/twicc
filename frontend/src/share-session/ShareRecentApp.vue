<script setup>
import { useI18n } from 'vue-i18n'
const { t, locale } = useI18n()
import { ref } from 'vue'
import { readRecentShares, removeRecentShare } from '../share-recent/recordView'

const entries = ref(readRecentShares())

function open(token) { window.location.assign(`/share/${token}/`) }
function remove(token) { removeRecentShare(token); entries.value = readRecentShares() }
function when(iso) { try { return new Date(iso).toLocaleString(locale.value) } catch { return '' } }
</script>

<template>
    <main class="share-recent">
        <h1>{{ $t('sharing.recent.title') }}</h1>
        <!-- The list is a purely local, per-browser convenience — no server-side
             per-viewer history exists (design §12), so say so plainly. -->
        <p class="note">
            {{ $t('sharing.recent.localHistory') }}
        </p>
        <p v-if="!entries.length" class="empty">{{ $t('sharing.recent.empty') }}</p>
        <ul v-else>
            <li v-for="e in entries" :key="e.token">
                <button class="row" type="button" @click="open(e.token)">
                    <wa-icon class="kind-icon" :name="e.kind === 'artifact' ? 'shapes' : 'comment'"></wa-icon>
                    <span class="body">
                        <span class="title">{{ e.title || e.token }}</span>
                        <span class="meta">{{ e.kind === 'artifact' ? $t('sharing.recent.artifactOpened', { date: when(e.lastAccess) }) : $t('sharing.recent.sessionOpened', { date: when(e.lastAccess) }) }}</span>
                    </span>
                </button>
                <wa-button size="small" appearance="plain" :title="$t('sharing.recent.remove')" @click="remove(e.token)">
                    <wa-icon name="xmark"></wa-icon>
                </wa-button>
            </li>
        </ul>
    </main>
</template>

<style scoped>
.share-recent { max-width: 640px; margin: 3rem auto; padding: 0 1rem; }
h1 { font-size: 1.4rem; margin-bottom: .4rem; }
.note { color: var(--wa-color-text-quiet, #888); font-size: .85rem; margin: 0 0 1.25rem; }
.empty { color: var(--wa-color-text-quiet, #888); }
ul { list-style: none; margin: 0; padding: 0; display: flex; flex-direction: column; gap: .4rem; }
li { display: flex; align-items: stretch; gap: .5rem; }
/* Uniform rows: a fixed-width icon column + a two-line body give every item the
   same height regardless of title length or kind. */
.row {
    flex: 1;
    min-width: 0;
    display: flex;
    align-items: center;
    gap: .7rem;
    min-height: 3.25rem;
    background: none;
    border: 1px solid var(--wa-color-surface-border, #333);
    border-radius: 8px;
    padding: .5rem .8rem;
    color: inherit;
    cursor: pointer;
    text-align: left;
}
.row:hover { background: var(--wa-color-surface-raised, rgba(255, 255, 255, .04)); }
.kind-icon {
    font-size: 1.15rem;
    color: var(--wa-color-text-quiet, #888);
    flex-shrink: 0;
    width: 1.4rem;
    text-align: center;
}
.body { flex: 1; min-width: 0; display: flex; flex-direction: column; gap: .15rem; }
.title { font-weight: 600; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.meta {
    font-size: .78rem;
    color: var(--wa-color-text-quiet, #888);
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
}
</style>
