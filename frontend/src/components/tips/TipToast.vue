<script setup>
import { ref, computed, watch, onBeforeUnmount } from 'vue'
import { useTipsStore } from '../../stores/tips'
import { useSettingsStore } from '../../stores/settings'
import { renderMarkdown } from '../../utils/markdown'
import { stripFrontMatter } from '../../utils/frontMatter'
import { resolvePublicAssetUrl } from '../../utils/publicAsset'
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

const tip = computed(() => {
    const k = tipsStore.currentToastTipKey
    if (!k) return null
    return { key: k, ...tipsStore.manifest[k] }
})

const bodyHtml = ref('')
const loading = ref(false)
const errored = ref(false)
const bodyCache = new Map()

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

async function loadBody(key) {
    if (bodyCache.has(key)) {
        bodyHtml.value = bodyCache.get(key)
        loading.value = false
        errored.value = false
        return
    }
    loading.value = true
    errored.value = false
    try {
        const r = await fetch(resolvePublicAssetUrl(`tips/${key}.md`))
        if (!r.ok) throw new Error(`HTTP ${r.status}`)
        const raw = await r.text()
        const body = stripFrontMatter(raw)
        // Tips are authored with soft line wraps for source readability; render
        // them with CommonMark paragraph behavior (soft wrap -> space, blank line
        // -> new paragraph) rather than the chat-style breaks: true.
        const html = await renderMarkdown(body, { softBreakAsSpace: true })
        const finalHtml = rewriteAssetUrls(html, { folder: 'tips' })
        bodyCache.set(key, finalHtml)
        bodyHtml.value = finalHtml
    } catch (e) {
        console.error('Failed to load tip', key, e)
        errored.value = true
    } finally {
        loading.value = false
    }
}

// Mark the current tip as seen. Called only on voluntary close / Next tip,
// never on display. A tip the user really wants back is re-openable from the
// Settings > Tips list, which also offers "Reset all seen tips".
function commitSeen(key) {
    if (!key) return
    tipsStore.markSeen(key)
}

// React to currentToastTipKey changes : load new body.
// No markSeen here — commit happens only on voluntary close / Next.
watch(() => tipsStore.currentToastTipKey, async (newKey) => {
    if (!newKey) return
    await loadBody(newKey)
}, { immediate: true })

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
                Tip
            </wa-tag>
            <span class="tip-title">{{ tip?.title }}</span>
        </header>

        <wa-divider></wa-divider>

        <div v-if="loading" class="tip-loading">Loading…</div>
        <div v-else-if="errored" class="tip-error">Failed to load tip content.</div>
        <div v-else class="tip-body" v-html="bodyHtml" @click="handleHelpLinkClick" />

        <template v-if="hasMoreCandidates">
            <wa-divider></wa-divider>

            <footer class="tip-footer">
                <wa-button size="small" @click="onNextTip">
                    Next tip
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
