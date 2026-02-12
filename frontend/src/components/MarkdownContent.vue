<script setup>
import { ref, watch, onMounted, nextTick } from 'vue'
import { renderMarkdown } from '../utils/markdown.js'
// Uses the combined version that includes both light and dark
// Then override with our theme file that uses [data-theme] without media queries
import 'github-markdown-css/github-markdown.css'
import '../styles/github-markdown-themes.css'

const props = defineProps({
    source: {
        type: String,
        required: true
    }
})

const emit = defineEmits(['rendered'])

const renderedHtml = ref('')
const container = ref(null)
const rendering = ref(true)

// Lazy-loaded mermaid instance (dynamic import to avoid ~500KB in main bundle)
let mermaidModule = null
let mermaidInitialized = false

async function getMermaid() {
    if (!mermaidModule) {
        mermaidModule = (await import('mermaid')).default
    }
    if (!mermaidInitialized) {
        mermaidInitialized = true
        mermaidModule.initialize({
            startOnLoad: false,
            theme: 'default',
            securityLevel: 'loose',
        })
    }
    return mermaidModule
}

// Render mermaid diagrams found in the parsed HTML
async function renderMermaidDiagrams() {
    if (!container.value) return

    const mermaidBlocks = container.value.querySelectorAll('code.language-mermaid')
    if (mermaidBlocks.length === 0) return

    const mermaid = await getMermaid()

    for (const block of mermaidBlocks) {
        const pre = block.closest('pre')
        if (!pre) continue

        const source = block.textContent
        const id = `mermaid-${Math.random().toString(36).slice(2, 11)}`

        try {
            const { svg } = await mermaid.render(id, source)
            const wrapper = document.createElement('div')
            wrapper.className = 'mermaid-diagram'
            wrapper.innerHTML = svg
            pre.replaceWith(wrapper)
        } catch {
            // If mermaid fails, leave the code block as-is (it will show as plain code)
            pre.classList.add('mermaid-error')
        }
    }
}

// Add data-language attribute to code blocks for the language label
function addLanguageLabels() {
    if (!container.value) return

    for (const pre of container.value.querySelectorAll('pre.shiki')) {
        const code = pre.querySelector('code[class*="language-"]')
        if (!code) continue
        const lang = code.className.match(/language-(\S+)/)?.[1]
        if (lang) pre.dataset.language = lang
    }
}

async function render() {
    rendering.value = true
    try {
        renderedHtml.value = await renderMarkdown(props.source)
        await nextTick()
        addLanguageLabels()
        await renderMermaidDiagrams()
    } finally {
        rendering.value = false
        emit('rendered')
    }
}

watch(() => props.source, render)
onMounted(render)
</script>

<template>
    <div
        ref="container"
        class="markdown-body"
        v-html="renderedHtml"
    ></div>
</template>

<style>
/* -------------------------------------------------------------------
   Styles NOT covered by github-markdown-css:
   Shiki syntax highlighting extras + Mermaid diagrams.
   Dark mode handled via class data-theme="dark" on <html> (set by main.js).
   NOT scoped — must penetrate v-html content.
   ------------------------------------------------------------------- */
.markdown-body {
    background: transparent;
    /* Override github-markdown-css fixed 16px to inherit from :root */
    font-size: 1rem;
}

/* -- Shiki-generated code blocks ------------------------------------- */
.markdown-body pre {
    padding: 16px;
    border-radius: 6px;
    overflow-x: auto;
    margin-top: 1em;
}
markdown-body .highlight pre, .markdown-body pre, .markdown-body code, .markdown-body tt {
    font-size: 100%;
}
.markdown-body pre.shiki[data-language]:not([data-language="text"]) {
    padding-top: 36px;
    position: relative;
}
.markdown-body pre.shiki[data-language]:not([data-language="text"])::before {
    content: attr(data-language);
    position: absolute;
    top: 8px;
    left: 16px;
    font-size: var(--wa-font-size-s);
    color: #656d76;
    text-transform: uppercase;
    font-family: var(--wa-font-sans);
}

/* -- Mermaid diagrams ------------------------------------------------ */
.markdown-body .mermaid-diagram {
    margin: 16px 0;
    text-align: center;
    overflow-x: auto;
}
.markdown-body .mermaid-diagram svg {
    max-width: 100%;
    height: auto;
}
.markdown-body pre.mermaid-error {
    border-left: 3px solid #d29922;
}

/* Dark tweak to handle dark mode https://shiki.style/guide/dual-themes */
.shiki, .shiki span {
    --shiki-bg-color: var(--wa-color-surface-default);
    background-color: var(--shiki-bg-color) !important;
}
html.wa-dark .shiki,
html.wa-dark .shiki span {
  color: var(--shiki-dark) !important;
  /* Optional, if you also want font styles */
  font-style: var(--shiki-dark-font-style) !important;
  font-weight: var(--shiki-dark-font-weight) !important;
  text-decoration: var(--shiki-dark-text-decoration) !important;
}

</style>
