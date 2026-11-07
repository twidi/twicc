<script setup>
import { computed, onBeforeUnmount } from 'vue'
import { useI18n } from 'vue-i18n'
import { useTipsStore } from '../../stores/tips'
import { useSettingsStore } from '../../stores/settings'
import { renderMarkdown } from '../../utils/markdown'
import { createLocalizedContentLoader } from '../../utils/localizedContent.js'
import { useLocalizedContentBody } from '../../composables/useLocalizedContentBody.js'
import { rewriteAssetUrls } from '../../utils/contentAssets'
import { handleHelpLinkClick } from '../../utils/helpLinks'
import { TIP_COOLDOWN_MS } from '../../composables/useTipScheduler'

// CustomNotification automatically injects the Notivue item as the `item` prop.
// (See CustomNotification.vue line 103 : <component … :item="item" />)
const props = defineProps({
    item: { type: Object, required: true },
})

const tipsStore = useTipsStore()
const settings = useSettingsStore()
const { locale } = useI18n()

const tip = computed(() => {
    const k = tipsStore.currentToastTipKey
    if (!k) return null
    return { key: k, ...tipsStore.manifest[k] }
})

const env = computed(() => ({
    platform: settings._isTouchDevice ? 'mobile' : 'desktop',
    os: settings.os,
    enabledProviders: settings.enabledProviders,
}))

const hasMoreCandidates = computed(() => {
    if (!tip.value) return false
    const candidates = tipsStore.getCandidates(env.value)
    return candidates.filter((c) => c.key !== tip.value.key).length > 0
})

const loadMarkdown = createLocalizedContentLoader('tips')
const currentKey = computed(() => tipsStore.currentToastTipKey)
const { bodyHtml, contentLocale, loading, errored } = useLocalizedContentBody(currentKey, locale, async (key, language) => {
    const content = await loadMarkdown(key, language)
    const html = await renderMarkdown(content.body, { softBreakAsSpace: true })
    return { html: rewriteAssetUrls(html, { folder: 'tips' }), locale: content.locale }
})

// Mark the current tip as seen. Called only on voluntary close / Next tip,
// never on display. A tip the user really wants back is re-openable from the
// Settings > Tips list, which also offers "Reset all seen tips".
function commitSeen(key) {
    if (!key) return
    tipsStore.markSeen(key)
}

// Tear down without re-committing (used after commitSeen has already run).
function teardown() {
    tipsStore.nextEligibleTime = Date.now() + TIP_COOLDOWN_MS
    tipsStore.currentToastTipKey = null
    props.item.clear()
}

function onClose() {
    commitSeen(tipsStore.currentToastTipKey)
    teardown()
}

// Catch-all for external dismissals — e.g. the user clicks Notivue's own
// "×" button (rendered by CustomNotification.vue), or the toast is cleared
// from elsewhere. teardown() has already run for our explicit paths
// (sets currentToastTipKey = null), so this only fires when the toast was
// dismissed externally without going through onClose / onNextTip's "no
// more candidates" branch.
onBeforeUnmount(() => {
    const key = tipsStore.currentToastTipKey
    if (key === null) return   // dismissed via our own teardown() — nothing to do
    commitSeen(key)
    tipsStore.nextEligibleTime = Date.now() + TIP_COOLDOWN_MS
    tipsStore.currentToastTipKey = null
    // Do NOT call props.item.clear() here — the toast is already being
    // unmounted ; calling clear() again would be redundant (and Notivue may
    // not be happy about it).
})

function onNextTip() {
    const key = tipsStore.currentToastTipKey
    commitSeen(key)
    const candidates = tipsStore.getCandidates(env.value)
    const next = tipsStore.pickRandom(candidates, [key])
    if (!next) {
        teardown()
        return
    }
    tipsStore.currentToastTipKey = next.key
}
</script>

<template>
    <div class="tip-toast" tabindex="-1" @keydown.esc="onClose">
        <header class="tip-header">
            <wa-tag size="medium" class="tip-badge" variant="brand">
                <wa-icon name="lightbulb" />
                {{ $t('tips.toast.label') }}
            </wa-tag>
            <span class="tip-title">{{ tip?.title }}</span>
        </header>

        <wa-divider></wa-divider>

        <div v-if="loading" class="tip-loading">{{ $t('tips.toast.loading') }}</div>
        <div v-else-if="errored" class="tip-error">{{ $t('tips.toast.loadFailed') }}</div>
        <div v-else class="tip-body" :lang="contentLocale" v-html="bodyHtml" @click="handleHelpLinkClick" />

        <template v-if="hasMoreCandidates">
            <wa-divider></wa-divider>

            <footer class="tip-footer">
                <wa-button size="small" @click="onNextTip">
                    {{ $t('tips.toast.next') }}
                    <wa-icon slot="end" name="chevron-right" />
                </wa-button>
            </footer>
        </template>
    </div>
</template>

<style>
/* Notivue's .Notivue__content-message has a hardcoded max-height of 250px
   (notivue/dist/Notifications/notifications.css) which clips our toast's
   footer (Next tip) on any tip whose body exceeds ~200px.
   Override for tip toasts only. The :has() selector targets the wrapper
   that contains our component root. The body keeps its own max-height so
   very long tips still scroll internally below the footer threshold. */
.Notivue__content-message:has(> .tip-toast) {
    max-height: none;
}

/* Hide Notivue's type-driven left icon (info/warning/error/...) on tip
   toasts only. We render our own lightbulb in the tip header and the
   framework icon is just visual noise here. `push[type]` requires a type,
   so we can't suppress the icon by passing none — CSS is the cleanest
   knob. `display: none` removes it from layout so the content flushes
   naturally against the left edge of the notification. */
.Notivue__notification:has(.tip-toast) .Notivue__icon {
    visibility: hidden;
}
</style>

<style scoped>
.tip-toast {
    display: flex;
    flex-direction: column;
    gap: 0.5rem;
    min-width: 0;
}

.tip-header {
    display: flex;
    align-items: center;
    gap: 0.5rem;
    font-weight: 600;
}

.tip-badge {
    gap: 0.25rem;
}

.tip-title {
    flex: 1;
    min-width: 0;
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
}

.tip-body {
    max-height: 60vh;
    overflow-y: auto;
}

.tip-body :deep(img) {
    max-width: 100%;
    height: auto;
}

.tip-footer {
    display: flex;
    align-items: center;
    justify-content: flex-end;
}

wa-divider {
    --spacing: 0.375rem;
}

.tip-loading,
.tip-error {
    padding: 0.5rem 0;
    font-style: italic;
    color: var(--wa-color-neutral-on-quiet, #888);
}
</style>
