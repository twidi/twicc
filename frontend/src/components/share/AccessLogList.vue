<script setup>
import { useI18n } from 'vue-i18n'
const { n, locale } = useI18n()
// Shared date/ip/user-agent access log list: per-share "Recent views"
// (ShareListPanel) and per-host denial details (ArtifactBookmarkDialog).
// Rows: { at, ip, user_agent, count? } — count > 1 shows a ×N badge.
import { ref } from 'vue'

defineProps({ entries: { type: Array, required: true } })
function when(iso) { try { return new Date(iso).toLocaleString(locale.value) } catch { return '' } }
const expandedUa = ref({})
function toggleUa(i) { expandedUa.value[i] = !expandedUa.value[i] }
</script>

<template>
    <ul class="access-log">
        <li v-for="(a, i) in entries" :key="i">
            <span class="when">{{ when(a.at) }}</span>
            <span v-if="a.count > 1" class="count">×{{ n(a.count) }}</span>
            <code v-if="a.ip">{{ a.ip }}</code>
            <span v-if="a.user_agent" class="ua" :class="{ 'ua--expanded': expandedUa[i] }"
                  role="button" tabindex="0"
                  :title="expandedUa[i] ? '' : $t('sharing.access.showUserAgent')"
                  @click="toggleUa(i)" @keydown.enter="toggleUa(i)">{{ a.user_agent }}</span>
        </li>
    </ul>
</template>

<style scoped>
/* Capped height: a busy log scrolls instead of blowing up the dialog. */
.access-log { list-style: none; margin: 0; padding: 0; display: flex; flex-direction: column; gap: 0.2rem; max-height: 14rem; overflow-y: auto; }
.access-log li { display: flex; gap: 0.6rem; align-items: baseline; }
.when { color: var(--wa-color-text-quiet); white-space: nowrap; }
.count { color: var(--wa-color-text-quiet); font-size: 0.75rem; }
.ua { flex: 1; min-width: 5rem; color: var(--wa-color-text-quiet); overflow: hidden; text-overflow: ellipsis; white-space: nowrap; cursor: pointer; }
.ua:hover { color: var(--wa-color-text); }
/* Expanded: reveal the full user agent, wrapped and selectable. */
.ua--expanded { overflow: visible; white-space: normal; word-break: break-word; }
</style>
