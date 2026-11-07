<script setup>
import { useI18n } from 'vue-i18n'
const { t } = useI18n()
import { computed, ref, onMounted } from 'vue'
import MarkdownContent from '../components/ui/MarkdownContent.vue'
// Singleton driven by MarkdownContent's openMediaPreview() — without it, clicking a
// rendered image or Mermaid SVG has nowhere to open. Mirrors ShareSessionApp.
import GlobalMediaPreview from '../components/media/GlobalMediaPreview.vue'
import ShareThemeToggle from './ShareThemeToggle.vue'
import ShareFooter from './ShareFooter.vue'

const props = defineProps({ tokenPath: String, meta: Object })
const source = ref('')
const error = ref(false)
const snapshotAt = ref(props.meta.snapshot_at)
const updated = ref(false)

// The doc view renders one of three shapes, tagged by the backend via meta.docKind:
//  - markdown → the file text, as-is
//  - mermaid  → raw Mermaid source, wrapped in a fenced ```mermaid block so
//               MarkdownContent's Mermaid pipeline turns it into an SVG (the same
//               path already used for diagrams inside shared `.md` files)
//  - image    → embedded as a Markdown image, so MarkdownContent renders it AND its
//               click-to-zoom/pan lightbox applies (never fetched as text — it's binary)
// Fallback keeps an older backend (docExt only, no docKind) working.
const docKind = props.meta.docKind
    || (props.meta.docExt === 'mmd' || props.meta.docExt === 'mermaid' ? 'mermaid' : 'markdown')

const imageMarkdown = computed(() => {
    // Keep provider titles intact and make the fallback image alt follow the locale.
    const alt = (props.meta.title || t('session.tools.image.name')).replace(/[[\]\n]/g, ' ')
    return `![${alt}](<${props.meta.docUrl}>)`
})

async function load() {
    if (docKind === 'image') return
    try {
        const res = await fetch(props.meta.docUrl, { credentials: 'same-origin' })
        if (!res.ok) throw new Error(String(res.status))
        const text = await res.text()
        source.value = docKind === 'mermaid' ? '```mermaid\n' + text + '\n```' : text
    } catch { error.value = true }
}
onMounted(load)

// Poll snapshot freshness (D7) while visible.
onMounted(() => {
    setInterval(async () => {
        if (document.hidden) return
        try {
            const m = await (await fetch(`${props.tokenPath}/api/artifact-meta/`, { credentials: 'same-origin' })).json()
            if (m.snapshot_at && m.snapshot_at !== snapshotAt.value) updated.value = true
        } catch { /* ignore */ }
    }, 30000)
})
</script>

<template>
    <div class="share-doc">
        <div class="share-doc-scroll">
            <div class="share-doc-body">
                <wa-callout v-if="updated" variant="brand" class="share-banner">
                    <i18n-t scope="global" keypath="sharing.document.updated">
                        <template #reload><a href="#" @click.prevent="location.reload()">{{ $t('sharing.actions.reload') }}</a></template>
                    </i18n-t>
                </wa-callout>
                <wa-callout v-if="error" variant="danger">{{ $t('sharing.document.unavailable') }}</wa-callout>
                <MarkdownContent v-else :source="docKind === 'image' ? imageMarkdown : source" :show-toolbar="false"
                                 :class="{ 'doc-image': docKind === 'image' }" />
            </div>
        </div>
        <ShareFooter solid>
            <ShareThemeToggle />
        </ShareFooter>
        <GlobalMediaPreview />
    </div>
</template>

<style>
/* Full-viewport flex column: the document scrolls INTERNALLY so the footer stays
   pinned at the bottom, mirroring the session and HTML-artifact share viewers
   (dynamic height via #app's 100dvh, defined in ShareSessionApp's global style). */
.share-doc { height: 100%; display: flex; flex-direction: column; }
.share-doc-scroll { flex: 1 1 auto; min-height: 0; overflow: auto; }
.share-doc-body { max-width: 55rem; margin: 0 auto; padding: 1.5rem; }

/* An image artifact is the whole document — center it, like the Mermaid diagram
   (.mermaid-diagram is centered in MarkdownContent). Only for the image doc, so
   inline images inside a shared `.md` keep their in-flow left alignment. */
.share-doc .doc-image .markdown-body img {
    display: block;
    margin-inline: auto;
}
</style>
