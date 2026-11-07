<script setup>
// HelpDialog.vue — the single, app-wide help dialog. Mounted once at the
// App root; it watches the help store's `currentHelpKey` and renders the
// matching `help/<key>.md` body. Opened either automatically by a code
// event (helpStore.maybeAutoShow) or manually (showHelp() from a button,
// the Settings → Help list, or a `help/<key>` cross-link).
//
// Whether the "Don't show this again" switch appears is decided by the
// call site (open()'s showSwitch arg, surfaced as currentHelpShowSwitch);
// it shows by default. When shown, leaving it on at close marks the page
// seen (it won't auto-open again) and turning it off keeps it in rotation;
// when hidden, the seen-state is left untouched.
import { ref, computed, watch, nextTick } from 'vue'
import { useI18n } from 'vue-i18n'
import { useHelpStore } from '../../stores/help'
import { renderMarkdown } from '../../utils/markdown'
import { createLocalizedContentLoader } from '../../utils/localizedContent.js'
import { useLocalizedContentBody } from '../../composables/useLocalizedContentBody.js'
import { rewriteAssetUrls } from '../../utils/contentAssets'
import { handleHelpLinkClick } from '../../utils/helpLinks'

const helpStore = useHelpStore()
const { locale } = useI18n()

const dialogRef = ref(null)
const dontShowAgain = ref(true)

const currentKey = computed(() => helpStore.currentHelpKey)
const meta = computed(() => (currentKey.value ? helpStore.manifest[currentKey.value] : null))
const title = computed(() => meta.value?.title || '')
// The switch visibility is a per-call decision carried by the store.
const showSwitch = computed(() => helpStore.currentHelpShowSwitch)

const loadMarkdown = createLocalizedContentLoader('help')
const { bodyHtml, contentLocale, loading, errored } = useLocalizedContentBody(currentKey, locale, async (key, language) => {
    const content = await loadMarkdown(key, language)
    const html = await renderMarkdown(content.body, { softBreakAsSpace: true })
    return { html: rewriteAssetUrls(html, { folder: 'help' }), locale: content.locale }
})

// Open / swap the dialog when the active help key changes. Swapping in
// place (e.g. following a cross-link) keeps the dialog open; clearing the
// key (close) leaves it closed.
watch(currentKey, async (key) => {
    if (!key) return
    dontShowAgain.value = true
    await nextTick()
    if (currentKey.value === key && dialogRef.value) dialogRef.value.open = true
}, { immediate: true })

function closeDialog() {
    if (dialogRef.value) dialogRef.value.open = false
}

// Single funnel for every close path (footer button, header ✕, Escape,
// backdrop). Guarded against bubbling wa-after-hide from any nested wa-*
// child (see CLAUDE.md "Bubbling custom events").
function onAfterHide(event) {
    if (event && event.target !== dialogRef.value) return
    const key = helpStore.currentHelpKey
    if (key && showSwitch.value) {
        if (dontShowAgain.value) helpStore.markSeen(key)
        else helpStore.unmarkSeen(key)
    }
    helpStore.close()
}
</script>

<template>
    <wa-dialog
        ref="dialogRef"
        :label="title"
        class="help-dialog"
        @wa-after-hide="onAfterHide"
    >
        <div v-if="loading" class="help-loading">{{ $t('help.dialog.loading') }}</div>
        <div v-else-if="errored" class="help-error">{{ $t('help.dialog.loadError') }}</div>
        <div v-else class="help-body" :lang="contentLocale" v-html="bodyHtml" @click="handleHelpLinkClick" />

        <div slot="footer" class="help-footer">
            <wa-switch
                v-if="showSwitch"
                :checked="dontShowAgain"
                @change="dontShowAgain = $event.target.checked"
                size="small"
            >
                {{ $t('help.dialog.dontShowAgain') }}
            </wa-switch>
            <wa-button variant="brand" @click="closeDialog">{{ $t('common.actions.close') }}</wa-button>
        </div>
    </wa-dialog>
</template>

<style scoped>
.help-dialog {
    --width: min(840px, calc(100vw - 2rem));
}

.help-body {
    line-height: 1.5;
}

.help-body :deep(img) {
    max-width: 100%;
    height: auto;
}

.help-body :deep(a[data-help-key]) {
    cursor: pointer;
}

/* The switch sits on the left, the button on the right — through an auto margin, not
   space-between: once the footer wraps (narrow screen), space-between would leave the
   lone button of the second line aligned to the start. */
.help-footer {
    display: flex;
    align-items: center;
    justify-content: flex-end;
    gap: 0.5rem;
}

.help-footer wa-switch {
    margin-inline-end: auto;
}

.help-loading,
.help-error {
    padding: 0.5rem 0;
    font-style: italic;
    color: var(--wa-color-neutral-on-quiet, #888);
}
</style>
