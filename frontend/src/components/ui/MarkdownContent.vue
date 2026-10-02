<script setup>
import { ref, computed, inject, watch, onMounted, nextTick } from 'vue'
import { useRouter } from 'vue-router'
import {
    splitMarkdownBlocks,
    renderBlockToHtml,
    extractHeadings,
    extractBlockquoteSources,
} from '../../utils/markdown.js'
import { hashString } from '../../utils/hash.js'
import { useSettingsStore } from '../../stores/settings'
import { vHighlight } from '../../directives/vHighlight.js'
import { toast } from '../../composables/useToast'
import { openMediaPreview } from '../../composables/useMediaPreview'
import { getMermaid, applyMermaidTheme } from '../../utils/mermaid'
// Uses the combined version that includes both light and dark
// Then override with our theme file that uses [data-color-scheme] without media queries
import 'github-markdown-css/github-markdown.css'
import '../../styles/github-markdown-themes.css'

const props = defineProps({
    source: {
        type: String,
        required: true
    },
    showToolbar: {
        type: Boolean,
        default: true
    },
    // Render a leading `/command` in the source as a styled tag (user messages
    // only — set by TextContent when the message starts with a slash command).
    tagSlashCommand: {
        type: Boolean,
        default: false
    },
    // Opt-in collapsible table of contents at the very top (closed by default).
    // Enabled by the file-preview panes (Files / Plan / Git .md diffs) where the
    // rendered document can be long; left off for chat, tips, changelog, etc.
    showToc: {
        type: Boolean,
        default: false
    }
})

const emit = defineEmits(['rendered'])

const router = useRouter()
const settingsStore = useSettingsStore()

// Search highlight terms injected from SessionItemsList (empty when no search active)
const highlightTerms = inject('searchHighlightTerms', ref([]))

// File-link resolution context, provided by SessionItemsList. Absent (null)
// when this component is mounted outside a session (e.g., whats-new, tips):
// in that case the post-processing step is skipped and links keep their
// default SPA behavior.
const fileLinks = inject('markdownFileLinks', null)

// Share-mode hook: rewrite in-content media URLs (e.g. /artifacts/<sid>/x.png →
// /share/<t>/media/x.png). Absent in the SPA (behaviour unchanged); when present,
// a null return marks the media as not-shared (rendered as a broken placeholder).
const rewriteContentMediaUrl = inject('rewriteContentMediaUrl', null)

const blocks = ref([])
const container = ref(null)
const rendering = ref(true)

// --- Table of contents (opt-in via showToc) --------------------------------
// The heading outline, recomputed on source change (only when the TOC is on, so
// there is zero parse cost for the many consumers that leave it off). Entries
// are matched to the rendered <h*> elements positionally, so no ids are needed.
const headings = ref([])
watch(
    [() => props.source, () => props.showToc],
    ([src, on]) => {
        headings.value = on ? extractHeadings(src) : []
    },
    { immediate: true },
)
// Shallowest heading level present, used as the indentation baseline so a doc
// starting at h2 doesn't render its whole outline pushed one level in.
const tocMinLevel = computed(() =>
    headings.value.reduce((min, h) => Math.min(min, h.level), 6),
)
// The TOC only earns its place once there are at least two headings to jump
// between.
const showTocDetails = computed(() => props.showToc && headings.value.length >= 2)

// Scroll the rendered heading at `index` to the top of the scroll viewport.
// The NodeList is re-queried on each click (robust to theme/mermaid re-renders),
// and scrollIntoView resolves the nearest scrollable ancestor on its own (the
// preview container, fullscreen wrapper included).
function scrollToHeading(index) {
    const root = container.value
    if (!root) return
    const els = root.querySelectorAll('h1, h2, h3, h4, h5, h6')
    els[index]?.scrollIntoView({ behavior: 'smooth', block: 'start' })
}

// Per-block rendered-HTML cache, keyed by raw block source (content-addressed:
// identical blocks share a render, and a key can never serve the wrong HTML).
// Component-level and non-reactive: it lives for the component's lifetime and is
// rebuilt on remount — matching the "streaming only" scope (no cross-mount cache).
const renderCache = new Map()

// Render mermaid diagrams found inside a (possibly detached) root node. Replaces
// each <pre><code class="language-mermaid"> with a <div class="mermaid-diagram">
// holding the SVG string returned by mermaid.render. Works on a detached node:
// mermaid renders into its own offscreen sandbox and returns a string, so the SVG
// is inlined into the block HTML before it ever reaches the live DOM (no flash).
// Returns true if every mermaid diagram rendered (or there were none), false if
// any failed — the caller uses this to avoid caching a failed render.
async function renderMermaidIn(root, theme) {
    const mermaidBlocks = root.querySelectorAll('code.language-mermaid')
    if (mermaidBlocks.length === 0) return true

    const mermaid = await getMermaid()
    let ok = true

    for (const block of mermaidBlocks) {
        const pre = block.closest('pre')
        if (!pre) continue

        const source = applyMermaidTheme(block.textContent, theme)
        const id = `twicc-mermaid-${Math.random().toString(36).slice(2, 11)}`

        try {
            const { svg } = await mermaid.render(id, source)
            const wrapper = document.createElement('div')
            wrapper.className = 'mermaid-diagram'
            wrapper.innerHTML = svg
            pre.replaceWith(wrapper)
        } catch {
            // If mermaid fails (e.g. an incomplete block mid-stream), leave the
            // code block as-is (it will show as plain code).
            pre.classList.add('mermaid-error')
            ok = false
        }
    }
    return ok
}

// Add data-language attribute to code blocks (inside the given root) for the label.
function addLanguageLabelsIn(root) {
    for (const pre of root.querySelectorAll('pre.shiki')) {
        const code = pre.querySelector('code[class*="language-"]')
        if (!code) continue
        const lang = code.className.match(/language-(\S+)/)?.[1]
        if (lang) pre.dataset.language = lang
    }
}

// Annotate file-path links (inside the given root) so handleLinkClick can route
// them to the Files tab. Skips external (http/mailto/...), anchor-only, and Vue
// Router routes.
function annotateFileLinksIn(root) {
    if (!fileLinks) return

    for (const a of root.querySelectorAll('a')) {
        if (a.getAttribute('target') === '_blank') continue
        const href = a.getAttribute('href')
        if (!href) continue
        if (/^[a-z][a-z0-9+.-]*:/i.test(href)) continue
        if (href.startsWith('#')) continue

        const result = fileLinks.classifyHref(href)
        if (result.kind === 'file') {
            a.setAttribute('data-file-candidates', JSON.stringify(result.candidates))
            if (result.lineNum != null) a.setAttribute('data-file-line', String(result.lineNum))
        } else if (result.kind === 'file-broken') {
            a.setAttribute('data-file-broken', 'true')
            a.removeAttribute('href')
        }
    }
}

// Rewrite <img src> through the injected share-mode hook. A null return means
// "not shared" → drop the src so the browser shows the alt text rather than a
// broken cross-origin request.
function rewriteContentMediaUrlsIn(root) {
    if (!rewriteContentMediaUrl) return
    for (const img of root.querySelectorAll('img')) {
        const src = img.getAttribute('src')
        if (!src) continue
        const next = rewriteContentMediaUrl(src)
        if (next == null) { img.removeAttribute('src'); img.setAttribute('data-media-unavailable', 'true') }
        else if (next !== src) img.setAttribute('src', next)
    }
}

// --- Per-block tools --------------------------------------------------------
//
// Some rendered blocks (code blocks, blockquotes) get a small hover toolbar,
// injected into the block's HTML during post-processing (same mechanism as the
// mermaid replacement above, so it lands in the cached string and survives Vue's
// v-html diffing). The buttons carry no listener of their own: clicks are caught
// by the container's existing delegation, next to the link/media handling, and
// routed by handleBlockToolsAction to the tools that own the button.
//
// One toolbar for every kind of block: same markup (`.block-tools-bar` holding
// `.block-tools-btn` buttons), same styles, same delegation — only the buttons
// and their actions differ.
//
// Part of the rendering engine, not an opt-in: wherever markdown is rendered —
// conversation, Plan tab, file preview, changelog, share pages — a code block
// is worth copying, and a markdown block is worth reading rendered.

// .floating-over-text (styles/transcript-tokens.css) is unconditional: the bar
// usually sits on the language-label row, which is empty on its right, but a
// fence with no language has no such row — and neither does the rendered view of
// a markdown block, nor a blockquote. In all of them, the buttons land on the
// first line of the content.
function blockToolsButton(action, icon, label) {
    return `<button type="button" class="block-tools-btn floating-over-text" data-block-action="${action}"`
        + ` title="${label}" aria-label="${label}"><wa-icon name="${icon}"></wa-icon></button>`
}

// The toolbar element for one block, ready to be placed by the caller. Falsy
// entries are dropped, so a conditional button can be passed inline.
function blockToolsBar(...buttons) {
    const bar = document.createElement('div')
    bar.className = 'block-tools-bar'
    bar.innerHTML = buttons.filter(Boolean).join('')
    return bar
}

// Route a toolbar click to the tools that own the button: its nearest ancestor
// is either a code block's wrapper or the blockquote itself (a code block inside
// a quote resolves to the wrapper, which is the closer of the two).
function handleBlockToolsAction(button) {
    const owner = button.closest('.code-tools, blockquote')
    if (!owner) return
    if (owner.classList.contains('code-tools')) handleCodeToolsAction(button)
    else handleQuoteToolsAction(button, owner)
}

// --- Per-code-block tools ---------------------------------------------------

// Fence languages whose blocks also get the "show rendered" toggle.
const MARKDOWN_LANGS = new Set(['markdown', 'md'])

// UI state per code block: `{ wrap?: boolean, rendered?: boolean }`, keyed by a
// hash of the block's code. Content-keyed rather than positional, because the
// block a user toggled keeps its identity as the message grows around it — and
// the whole toolbar is recreated whenever v-html replaces the block (the
// streaming one does, every frame). Two identical code blocks in one message
// share an entry: they are indistinguishable by content, and the only cost is a
// toggle applying to both. Non-reactive: the DOM it drives lives outside Vue.
const codeToolsState = new Map()

// Wrap every code block (inside the given root) in a `.code-tools` container
// holding its toolbar. Runs after addLanguageLabelsIn, so `data-language` is
// already set and the markdown-only button can be decided here.
function addCodeToolsIn(root) {
    for (const pre of root.querySelectorAll('pre')) {
        // Already wrapped — happens when a nested render re-processes a subtree.
        if (pre.parentElement?.classList.contains('code-tools')) continue
        const code = pre.textContent ?? ''
        if (!code.trim()) continue

        const wrapper = document.createElement('div')
        wrapper.className = 'code-tools'
        wrapper.dataset.codeKey = hashString(code)

        const bar = blockToolsBar(
            MARKDOWN_LANGS.has(pre.dataset.language ?? '')
                ? blockToolsButton('view', 'eye', 'Show rendered markdown')
                : '',
            blockToolsButton('wrap', 'text-width', 'Toggle line wrapping'),
            blockToolsButton('copy', 'copy', 'Copy code'),
        )

        pre.replaceWith(wrapper)
        wrapper.append(bar, pre)
    }
}

function codeToolsParts(wrapper) {
    return {
        pre: wrapper.querySelector(':scope > pre'),
        wrapBtn: wrapper.querySelector(':scope > .block-tools-bar > [data-block-action="wrap"]'),
        viewBtn: wrapper.querySelector(':scope > .block-tools-bar > [data-block-action="view"]'),
        view: wrapper.querySelector(':scope > .code-tools-rendered'),
    }
}

// Force line wrapping on or off for one block. Written as an inline style rather
// than a class because the ambient rules differ per consumer (user messages wrap
// their code blocks by default — see TextContent.vue), and an explicit toggle
// must win over any of them in both directions. `code` is set alongside `pre`
// for the same reason: an ambient rule targeting it would otherwise beat
// inheritance when we switch wrapping off.
function applyCodeWrap(wrapper, wrapped) {
    const { pre, wrapBtn } = codeToolsParts(wrapper)
    if (!pre) return
    for (const el of [pre, ...pre.querySelectorAll('code')]) {
        el.style.whiteSpace = wrapped ? 'pre-wrap' : 'pre'
        el.style.overflowWrap = wrapped ? 'break-word' : ''
    }
    wrapBtn?.classList.toggle('is-active', wrapped)
}

// Render a nested markdown document (the content of a ```markdown block) with
// the exact pipeline used for the outer content, tools included: a markdown
// block inside a markdown block stays explorable all the way down.
async function renderNestedMarkdown(source, theme) {
    const tmp = document.createElement('div')
    tmp.innerHTML = await renderBlockToHtml(source, {})
    await postProcessIn(tmp, theme, source)
    return tmp.innerHTML
}

// Show or hide the rendered view of a ```markdown block. The rendered HTML is
// built once per wrapper and then kept in the DOM, so flipping back and forth
// costs nothing. Returns false when the state could not be applied — the caller
// then leaves (or drops) the remembered toggle rather than lying about it.
async function applyCodeRendered(wrapper, rendered) {
    const { pre, viewBtn } = codeToolsParts(wrapper)
    if (!pre || !viewBtn) return false

    if (rendered && !codeToolsParts(wrapper).view) {
        let html
        try {
            html = await renderNestedMarkdown(pre.textContent ?? '', mermaidTheme())
        } catch {
            // Keep the raw block shown rather than swapping in an empty frame.
            toast.error('Could not render this markdown block', { duration: 3000 })
            return false
        }
        // The block may have been replaced (streaming) while the nested render
        // was in flight, or another call may have won the race to build the view.
        if (!wrapper.isConnected) return false
        if (!codeToolsParts(wrapper).view) {
            const view = document.createElement('div')
            view.className = 'code-tools-rendered'
            view.innerHTML = html
            wrapper.append(view)
        }
    }

    wrapper.classList.toggle('is-rendered', rendered)
    viewBtn.querySelector('wa-icon')?.setAttribute('name', rendered ? 'code' : 'eye')
    const label = rendered ? 'Show raw markdown' : 'Show rendered markdown'
    viewBtn.setAttribute('title', label)
    viewBtn.setAttribute('aria-label', label)
    return true
}

async function handleCodeToolsAction(button) {
    const wrapper = button.closest('.code-tools')
    if (!wrapper) return
    const { pre } = codeToolsParts(wrapper)
    if (!pre) return

    const key = wrapper.dataset.codeKey
    const state = codeToolsState.get(key) ?? {}

    switch (button.dataset.blockAction) {
        case 'copy':
            await copyText(pre.textContent ?? '', 'Code')
            break
        case 'wrap': {
            // Read the effective value rather than our own state: with no
            // explicit toggle yet, the ambient stylesheet decides, and the first
            // click must flip what the user actually sees.
            const wrapped = getComputedStyle(pre).whiteSpace !== 'pre'
            codeToolsState.set(key, { ...state, wrap: !wrapped })
            applyCodeWrap(wrapper, !wrapped)
            break
        }
        case 'view': {
            const next = !wrapper.classList.contains('is-rendered')
            if (await applyCodeRendered(wrapper, next)) {
                codeToolsState.set(key, { ...state, rendered: next })
            }
            break
        }
    }
}

// Re-apply the remembered toggles to the freshly rendered DOM. Only the block
// that changed was replaced, so this is a no-op for every other wrapper.
async function restoreCodeToolsState() {
    const root = container.value
    if (!root) return
    for (const wrapper of root.querySelectorAll('.code-tools[data-code-key]')) {
        const state = codeToolsState.get(wrapper.dataset.codeKey)
        if (!state) continue
        if (state.wrap !== undefined) applyCodeWrap(wrapper, state.wrap)
        // A block whose nested render fails forgets the toggle, so the failure
        // is reported once instead of on every subsequent render.
        if (state.rendered && !await applyCodeRendered(wrapper, true) && wrapper.isConnected) {
            codeToolsState.delete(wrapper.dataset.codeKey)
        }
    }
}

// --- Per-blockquote tools ---------------------------------------------------
//
// The same toolbar, with a single Copy button. What it copies is the quote's own
// markdown source with the `>` markers removed — the analogue of copying a code
// block without its fence, so emphasis, links and lists survive the round trip.
//
// The sources come from the block's raw markdown (extractBlockquoteSources) and
// are paired with the rendered <blockquote> elements positionally; the pairing is
// resolved at render time and the result carried by the quote itself, since the
// raw markdown is out of reach by the time a button is clicked.
//
// No wrapper element, unlike the code blocks: a quote never scrolls, so the bar
// anchors on the quote itself and the surrounding DOM stays untouched.
function addQuoteToolsIn(root, src) {
    const quotes = root.querySelectorAll('blockquote')
    if (!quotes.length) return

    // Both lists are in document order, so they align one to one — unless
    // something upstream (sanitizing, a future plugin) dropped a quote. Then no
    // source can be trusted, and every quote falls back to its rendered text.
    const sources = extractBlockquoteSources(src ?? '')
    const aligned = sources.length === quotes.length

    quotes.forEach((quote, index) => {
        // Already equipped — happens when a nested render re-processes a subtree.
        if (quote.querySelector(':scope > .block-tools-bar')) return
        const source = aligned ? sources[index] : ''
        if (!source.trim() && !(quote.textContent ?? '').trim()) return
        if (source) quote.dataset.quoteSource = source
        quote.prepend(blockToolsBar(blockToolsButton('copy', 'copy', 'Copy quote')))
    })
}

function handleQuoteToolsAction(button, quote) {
    if (button.dataset.blockAction !== 'copy') return
    copyText(quote.dataset.quoteSource ?? (quote.textContent ?? '').trim(), 'Quote')
}

// Matches a fenced mermaid block (``` or ~~~) at the start of a line. No `g`
// flag, so `.test()` stays stateless across calls.
const MERMAID_FENCE_RE = /(?:^|\n)[ \t]*(?:`{3,}|~{3,})[ \t]*mermaid\b/i

// Cache key for a block's rendered HTML. Mermaid blocks render to theme-specific
// SVG, so their key folds in the active theme; every other block renders
// identically in light and dark (Shiki ships dual-theme CSS), so it keys on the
// raw source alone and stays a cache hit across a theme toggle. A block rendered
// with the slash-command tag gets a NUL-prefixed key (NUL can't appear in
// source), so an identical block without the tag can never hit its cache entry.
function cacheKeyFor(src, theme, slashTag = false) {
    const key = MERMAID_FENCE_RE.test(src) ? `${theme} ${src}` : src
    return slashTag ? `\x00${key}` : key
}

// Everything that turns freshly parsed markdown HTML into its final shape. Runs
// on a DETACHED node, so the result is complete before it reaches the DOM — no
// flash, even on first paint. `src` is the raw markdown `root` was rendered
// from, which the quote tools need to recover each quote's own source. Returns
// false if a mermaid diagram failed, which makes the result unfit for caching.
async function postProcessIn(root, theme, src) {
    const mermaidOk = await renderMermaidIn(root, theme)
    addLanguageLabelsIn(root)
    annotateFileLinksIn(root)
    if (rewriteContentMediaUrl) rewriteContentMediaUrlsIn(root)
    addCodeToolsIn(root)
    addQuoteToolsIn(root, src)
    return mermaidOk
}

// Render one block to its final HTML, memoized by source (plus the active theme
// for mermaid blocks).
async function renderOneBlock(src, env, theme, slashTag = false) {
    const key = cacheKeyFor(src, theme, slashTag)
    const cached = renderCache.get(key)
    if (cached !== undefined) return cached

    const tmp = document.createElement('div')
    tmp.innerHTML = await renderBlockToHtml(src, slashTag ? { ...env, tagLeadingSlashCommand: true } : env)
    const mermaidOk = await postProcessIn(tmp, theme, src)
    const html = tmp.innerHTML

    // Cache only a fully-successful render. A failed mermaid (incomplete block
    // mid-stream, or a transient during concurrent renders) must stay retryable,
    // never frozen into the cache as an error state.
    if (mermaidOk) renderCache.set(key, html)
    return html
}

// Mermaid's native theme matching the app's current color scheme: 'dark' for
// dark mode, 'default' (Mermaid's light theme) otherwise. Captured once per
// render() so the theme used to render and the theme folded into the cache key
// can never diverge mid-render.
function mermaidTheme() {
    return settingsStore._effectiveColorScheme === 'dark' ? 'dark' : 'default'
}

// Monotonic render token: while streaming, render() fires every animation frame
// and successive calls overlap on their awaits. Only the latest call may commit
// its result / evict / emit, so a slow older frame can never clobber a newer one.
let renderSeq = 0

async function render() {
    rendering.value = true
    const mySeq = ++renderSeq
    const theme = mermaidTheme()
    try {
        const { blocks: raw, env } = splitMarkdownBlocks(props.source)

        // Sequential (not Promise.all): the streaming hot path is cache-hit
        // dominated (only the last block actually renders), and going one block
        // at a time sidesteps any concurrent mermaid.render concerns.
        const occurrences = new Map()
        const result = []
        for (const [i, block] of raw.entries()) {
            // The slash-command tag only ever applies to the very first block.
            const slashTag = props.tagSlashCommand && i === 0
            const html = await renderOneBlock(block.src, env, theme, slashTag)
            // Disambiguate identical blocks (e.g. two `---`) for a unique Vue key.
            const n = occurrences.get(block.hash) ?? 0
            occurrences.set(block.hash, n + 1)
            result.push({ key: `${block.hash}:${n}`, html })
        }

        // A newer render() superseded us mid-await: drop our now-stale result.
        if (mySeq !== renderSeq) return

        // Evict cache entries whose block is gone — chiefly the growing last
        // block, which otherwise leaves one stale entry per intermediate version.
        // Keys are theme-aware for mermaid blocks, so a theme toggle also evicts
        // the previous theme's now-superseded mermaid renders here.
        const liveKeys = new Set(raw.map((block, i) => cacheKeyFor(block.src, theme, props.tagSlashCommand && i === 0)))
        for (const key of renderCache.keys()) {
            if (!liveKeys.has(key)) renderCache.delete(key)
        }

        blocks.value = result

        // Re-apply the per-code-block toggles once the DOM catches up. Not
        // awaited: a nested markdown render must not delay the `rendered` event
        // consumers use for scroll anchoring.
        if (codeToolsState.size > 0) {
            nextTick(() => { if (mySeq === renderSeq) restoreCodeToolsState() })
        }
    } finally {
        // Only the latest render owns the lifecycle flag and the event.
        if (mySeq === renderSeq) {
            rendering.value = false
            emit('rendered')
        }
    }
}

// Re-render on source changes and on color-scheme changes: mermaid diagrams are
// static SVG baked at render time, so they need a fresh render to follow a
// dark/light toggle (non-mermaid blocks stay cache hits — see cacheKeyFor).
watch([() => props.source, () => settingsStore._effectiveColorScheme], render)
onMounted(render)

const showRaw = ref(false)

function toggleRaw() {
    showRaw.value = !showRaw.value
}

// Copy `text`, then report the outcome. `navigator.clipboard` only exists in a
// secure context, and TwiCC is regularly reached over plain HTTP on a LAN, so a
// hidden textarea + execCommand carries the fallback (same recipe as the
// access-blocked screen in main.js).
async function copyText(text, label) {
    let ok = false
    try {
        if (navigator.clipboard && window.isSecureContext) {
            await navigator.clipboard.writeText(text)
            ok = true
        } else {
            const textarea = document.createElement('textarea')
            textarea.value = text
            textarea.style.position = 'fixed'
            textarea.style.opacity = '0'
            document.body.appendChild(textarea)
            textarea.select()
            ok = document.execCommand('copy')
            textarea.remove()
        }
    } catch {
        ok = false
    }
    if (ok) toast.success(`${label} copied to clipboard`, { duration: 2000 })
    else toast.error(`Could not copy the ${label.toLowerCase()}`, { duration: 3000 })
}

function copySource() {
    copyText(props.source, 'Markdown')
}

// Selector for media that should open in MediaPreviewDialog when clicked:
// any <img> rendered from markdown, plus the SVG produced by Mermaid (which
// lives inside .mermaid-diagram). Restricting SVGs to .mermaid-diagram avoids
// hijacking unrelated inline SVGs (e.g. shiki / icon SVGs) that might be
// injected by future plugins.
const MEDIA_SELECTOR = 'img, .mermaid-diagram svg'

// Serialize an inline SVG (e.g. a Mermaid diagram) into a self-contained
// data URL the dialog's <img> can render. We use percent-encoded utf-8
// (data:image/svg+xml;charset=utf-8,...) rather than base64 because it is
// simpler, avoids encoding pitfalls with Unicode, and is universally supported.
//
// Width/height are forced from the viewBox: when an SVG is embedded in
// <img src="data:...">, percentage dimensions (Mermaid emits width="100%")
// have no containing block to resolve against and collapse to 0, making the
// image render as a 0×0 sliver. Explicit pixel dimensions give the <img>
// an intrinsic size the dialog can measure for its fit-content panel.
function svgToDataUrl(svg) {
    const clone = svg.cloneNode(true)
    if (!clone.getAttribute('xmlns')) {
        clone.setAttribute('xmlns', 'http://www.w3.org/2000/svg')
    }
    const vb = clone.getAttribute('viewBox')
    if (vb) {
        const parts = vb.trim().split(/[\s,]+/).map(Number)
        if (parts.length === 4 && parts.every(Number.isFinite)) {
            const [, , w, h] = parts
            if (w > 0) clone.setAttribute('width', String(w))
            if (h > 0) clone.setAttribute('height', String(h))
        }
    }
    const xml = new XMLSerializer().serializeToString(clone)
    return `data:image/svg+xml;charset=utf-8,${encodeURIComponent(xml)}`
}

// Build a MediaItem for an <img>. Walks up to a wrapping <a> (if any) to
// preserve the link as an item-level attribute so the dialog can offer
// "Open link". Skips file-link annotations (data-file-candidates /
// data-file-broken) since those aren't real URLs the user can open.
function buildImgItem(img) {
    if (!img.src) return null
    const anchor = img.closest('a')
    let link = null
    if (anchor
        && !anchor.hasAttribute('data-file-candidates')
        && !anchor.hasAttribute('data-file-broken')) {
        link = anchor.getAttribute('href') || null
    }
    return {
        type: 'image',
        src: img.src,
        name: img.getAttribute('alt') || 'Image',
        link,
    }
}

function buildSvgItem(svg) {
    const src = svgToDataUrl(svg)
    if (!src) return null
    return {
        type: 'image',
        src,
        name: 'Mermaid diagram',
        link: null,
    }
}

// Scan the rendered markdown for every media element (in DOM order) and
// build the items list passed to the preview dialog. Also locates the index
// of the clicked element within the filtered list so navigation lands on it.
function buildMediaItems(root, clickedEl) {
    const nodes = Array.from(root.querySelectorAll(MEDIA_SELECTOR))
    const items = []
    let clickedIndex = 0
    for (const node of nodes) {
        const item = node.tagName === 'IMG' ? buildImgItem(node) : buildSvgItem(node)
        if (!item) continue
        if (node === clickedEl) clickedIndex = items.length
        items.push(item)
    }
    return { items, clickedIndex }
}

// Route clicks on the rendered content:
//   - data-block-action   → per-block toolbar (code: copy / wrap / render,
//                            blockquote: copy)
//   - <img> or Mermaid SVG → open MediaPreviewDialog (wins over link nav,
//                            but the wrapping <a href>'s URL is preserved
//                            as an "Open link" affordance inside the dialog)
//   - data-file-candidates → open in Files tab via injected openFile
//   - data-file-broken     → swallow the click (link is rendered as plain text)
//   - external / mailto    → leave the browser to handle (target=_blank or default)
//   - anchor-only (#…)     → leave the browser to scroll
//   - everything else      → SPA navigation via router.push
function handleLinkClick(event) {
    // Per-block toolbar (code block, blockquote): checked first, since its
    // buttons sit inside the rendered content and must never reach the link or
    // media handling below.
    const toolsButton = event.target.closest('[data-block-action]')
    if (toolsButton && container.value?.contains(toolsButton)) {
        event.preventDefault()
        event.stopPropagation()
        handleBlockToolsAction(toolsButton)
        return
    }

    // Image / Mermaid SVG: open the preview dialog and short-circuit. The
    // check runs first so an image wrapped in <a> still opens the dialog
    // (the link remains reachable via the dialog's "Open link" button).
    const media = event.target.closest(MEDIA_SELECTOR)
    if (media && container.value && container.value.contains(media)) {
        event.preventDefault()
        event.stopPropagation()
        const { items, clickedIndex } = buildMediaItems(container.value, media)
        if (items.length > 0) {
            openMediaPreview(items, clickedIndex)
        }
        return
    }

    const anchor = event.target.closest('a')
    if (!anchor) return

    if (anchor.getAttribute('data-file-broken') === 'true') {
        event.preventDefault()
        return
    }

    const candidatesAttr = anchor.getAttribute('data-file-candidates')
    if (candidatesAttr) {
        event.preventDefault()
        const lineAttr = anchor.getAttribute('data-file-line')
        const lineNum = lineAttr ? parseInt(lineAttr, 10) : null
        let candidates = null
        try {
            candidates = JSON.parse(candidatesAttr)
        } catch {
            candidates = null
        }
        if (candidates?.length) fileLinks?.openFile?.(candidates, { lineNum })
        return
    }

    if (anchor.getAttribute('target') === '_blank') return

    const href = anchor.getAttribute('href')
    if (!href) return

    // Skip non-http(s) protocols (mailto:, tel:, etc.)
    if (/^[a-z][a-z0-9+.-]*:/i.test(href) && !/^https?:/i.test(href)) return

    // Let the browser scroll for in-page anchors
    if (href.startsWith('#')) return

    // Let the browser handle modifier clicks (open in new tab)
    if (event.ctrlKey || event.metaKey || event.shiftKey || event.altKey || event.button !== 0) return

    event.preventDefault()
    if (router) {
        router.push(href)
    } else if (/^https?:/i.test(href)) {
        // Router-less host (share bundle): open absolute links in a new tab; a
        // relative SPA route has no meaning here, so it stays inert.
        window.open(href, '_blank', 'noopener,noreferrer')
    }
}
</script>

<template>
    <div class="markdown-content-wrapper">
        <wa-details v-if="showTocDetails" class="markdown-toc" summary="Table of contents">
            <ul class="markdown-toc-list">
                <li
                    v-for="(h, i) in headings"
                    :key="i"
                    :style="{ paddingInlineStart: `${(h.level - tocMinLevel) * 1}rem` }"
                >
                    <button type="button" class="markdown-toc-link" @click="scrollToHeading(i)">
                        {{ h.text || 'Untitled' }}
                    </button>
                </li>
            </ul>
        </wa-details>
        <div v-if="showToolbar" class="markdown-toolbar">
            <wa-button-group orientation="vertical" label="Markdown tools">
                <wa-button
                    size="small"
                    :variant="showRaw ? 'neutral' : 'brand'"
                    appearance="filled"
                    :title="showRaw ? 'Show rendered markdown' : 'Show raw markdown'"
                    @click="toggleRaw"
                >
                    <wa-icon :name="showRaw ? 'code' : 'eye'"></wa-icon>
                </wa-button>
                <wa-button
                    size="small"
                    variant="neutral"
                    appearance="filled"
                    title="Copy raw markdown"
                    @click="copySource"
                >
                    <wa-icon name="copy"></wa-icon>
                </wa-button>
            </wa-button-group>
        </div>
        <pre v-if="showRaw" class="markdown-raw">{{ source }}</pre>
        <div
            v-show="!showRaw"
            ref="container"
            class="markdown-body"
            v-highlight="highlightTerms"
            @click="handleLinkClick"
        >
            <div
                v-for="block in blocks"
                :key="block.key"
                class="markdown-block"
                v-html="block.html"
            ></div>
        </div>
    </div>
</template>

<style>
/* The tinted blocks (blockquotes, `:::` containers, `::` lines) and the quote boxes of the Peer
   inbox and the text-selection comment share styles/quote-card.css. Inlined at the top of this
   block so it keeps its place after github-markdown-css (same specificity). */
@import '../../styles/quote-card.css';

/* -------------------------------------------------------------------
   Styles NOT covered by github-markdown-css:
   Shiki syntax highlighting extras + Mermaid diagrams.
   Dark mode handled via class data-color-scheme="dark" on <html> (set by main.js).
   NOT scoped — must penetrate v-html content.
   ------------------------------------------------------------------- */
.markdown-content-wrapper {
    position: relative;
}

/* -- Table of contents (opt-in, preview panes) ----------------------- */
.markdown-toc {
    margin-bottom: 1rem;
    font-size: var(--wa-font-size-s);
}
.markdown-toc-list {
    list-style: none;
    margin: 0;
    padding: 0;
    display: flex;
    flex-direction: column;
    /* Small gap for breathing room between entries (the row height itself stays
       tight — see the line-height note below). */
    gap: 0.45rem;
    /* Tight line-height inherited by the <li>: since each entry is a
       block-level button, the <li>'s own line-box strut (which uses this
       inherited value, not the button's) is what sets the row height. */
    line-height: 1.25;
}
.markdown-toc-link {
    display: block;
    width: 100%;
    /* A global `button` rule forces a form-control height (~43px); reset it so
       the row height follows the (tight) line-height instead. */
    height: auto;
    padding: 0;
    margin: 0;
    border: none;
    background: none;
    font: inherit;
    line-height: 1.25;
    text-align: start;
    color: var(--wa-color-brand-fill-loud);
    cursor: pointer;
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
}
.markdown-toc-link:hover {
    text-decoration: underline;
}

.markdown-body {
    background: transparent;
    /* Override github-markdown-css fixed 16px to inherit from :root */
    font-size: 1rem;
    /* Prose keeps proportional digits (the app sets tabular-nums on body). */
    font-variant-numeric: normal;
}

/* No lonely last word at the end of a paragraph. */
.markdown-body :is(p, li) {
    text-wrap: pretty;
}

/* Each top-level markdown block renders in its own keyed wrapper so Vue diffs
   blocks independently (no full re-render / Mermaid flash while streaming).
   `display: contents` removes the wrapper from the box tree, so github-markdown-css's
   direct-child rules and inter-block margin-collapsing behave as if unwrapped. */
.markdown-body > .markdown-block {
    display: contents;
}
/* Re-apply github-markdown-css's first/last-child margin reset one level deeper:
   `.markdown-body > *:first-child` now matches the (box-less) wrapper, not the
   block element inside it. */
.markdown-body > .markdown-block:first-child > :first-child {
    margin-top: 0 !important;
}
.markdown-body > .markdown-block:last-child > :last-child {
    margin-bottom: 0 !important;
}

/* -- Floating toolbar (raw toggle + copy) ---------------------------- */
.markdown-toolbar {
    position: absolute;
    top: 0;
    right: 0;
    padding: 0;
    background: transparent;
    border: none;
    opacity: 0;
    transform: scale(0.8);
    transform-origin: top right;
    transition: opacity 0.15s ease;
    z-index: 2;
}
.markdown-content-wrapper:hover .markdown-toolbar {
    opacity: 0.3;
}
.markdown-toolbar:hover {
    opacity: 1 !important;
}
@media (pointer:coarse) {
    .markdown-content-wrapper:focus {
        opacity: 1 !important;
    }
}
.markdown-toolbar wa-button wa-icon {
    width: .6rem;
}

.markdown-raw {
    margin: 0;
    padding: 0;
    background: transparent;
    overflow-x: auto;
    white-space: pre-wrap;
    word-break: break-word;
    font-family: var(--wa-font-family-body);
    font-size: 1rem;
    color: var(--wa-color-text-normal);
}

/* -- Tinted blocks: blockquotes and colon blocks --------------------- */
/* One recipe for all three (`:::` containers, `::` lines, plain markdown blockquotes) and for the
   quote boxes of the Peer inbox and the text-selection comment: styles/quote-card.css, inlined
   here so it keeps its place after github-markdown-css. */
/* No box inside the box: an id or a path keeps its monospace, drops its chip
   and inherits the header's text colour (the default would be unreadable on a
   loud fill). */
.markdown-body .md-line code {
    padding: 0;
    background: none;
    color: inherit;
    font-size: 1em;
}

/* Leading /command of a user message, rendered as a tag (the slash_command_tag
   rule in utils/markdown.js). A chip, because it is always one short token. */
.markdown-body .slash-command-tag {
    display: inline-block;
    padding: 0.05em 0.45em;
    border-radius: var(--wa-border-radius-s);
    background: var(--wa-color-brand-fill-quiet);
    border: 1px solid var(--wa-color-brand-border-quiet);
    color: var(--wa-color-brand-on-quiet);
    font-family: var(--wa-font-family-code);
    font-size: 0.875em;
}

/* Links and tables in the app's colours: github-markdown-css paints links GitHub blue and
   zebra-stripes the tables in two greys. The link is the theme's own link colour (the accent, a
   shade darker in light and lighter in dark); the rows are a faint accent tint over whatever
   surface the table sits on, the header a little stronger, the rules the app's divider colour.
   Same specificity as github-markdown-css, later: the user's filled bubble (SessionItem.vue)
   still wins with its own, more specific rules. */
.markdown-body a {
    color: var(--wa-color-text-link);
}
.markdown-body table th,
.markdown-body table td {
    border-color: var(--divider-color, var(--wa-color-surface-border));
}
.markdown-body table tr {
    background-color: transparent;
    border-top-color: var(--divider-color, var(--wa-color-surface-border));
}
.markdown-body table tr:nth-child(2n) {
    background-color: color-mix(in oklab, var(--wa-color-brand-60) 6%, transparent);
}
.markdown-body table th {
    background-color: color-mix(in oklab, var(--wa-color-brand-60) 12%, transparent);
}
.wa-dark .markdown-body table tr:nth-child(2n) {
    background-color: color-mix(in oklab, var(--wa-color-brand-60) 10%, transparent);
}
.wa-dark .markdown-body table th {
    background-color: color-mix(in oklab, var(--wa-color-brand-60) 18%, transparent);
}

/* The source stays lowercase so the raw text reads as a plain English phrase. */
.markdown-body .md-container-label::first-letter,
.markdown-body .md-line::first-letter {
    text-transform: uppercase;
}

/* -- Shiki-generated code blocks ------------------------------------- */
.markdown-body pre {
    padding: 16px;
    overflow-x: auto;
    margin-top: 1em;
}
.markdown-body .highlight pre, .markdown-body pre, .markdown-body code, .markdown-body tt {
    font-size: inherit !important;
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

/* -- Per-block toolbar (code blocks, blockquotes) --------------------- */
/* One look for every block that carries a toolbar: `.block-tools-bar` and
   `.block-tools-btn` are shared, only the host and its buttons differ. */
.markdown-body .block-tools-bar {
    position: absolute;
    top: 6px;
    right: 6px;
    display: flex;
    gap: 2px;
    opacity: 0;
    transition: opacity 0.15s ease;
    z-index: 2;
}
.markdown-body .code-tools:hover > .block-tools-bar,
.markdown-body blockquote:hover > .block-tools-bar,
.markdown-body .block-tools-bar:focus-within {
    opacity: 1;
}
/* No hover on touch: keep the bar visible but discreet. */
@media (pointer: coarse) {
    .markdown-body .block-tools-bar {
        opacity: 0.55;
    }
}
.markdown-body .block-tools-btn {
    /* A global `button` rule forces a form-control height (~43px); an explicit
       box is what keeps these compact. */
    display: inline-flex;
    align-items: center;
    justify-content: center;
    width: 1.5rem;
    height: 1.5rem;
    padding: 0;
    border: 1px solid var(--wa-color-neutral-border-quiet);
    border-radius: var(--wa-border-radius-s);
    color: var(--wa-color-text-quiet);
    font-size: 0.7rem;
    line-height: 1;
    cursor: pointer;
}
.markdown-body .block-tools-btn:hover {
    border-color: var(--wa-color-neutral-border-normal);
    color: var(--wa-color-text-normal);
}
/* No fill of its own: the background belongs to .floating-over-text, so the
   active state is carried by the border and the icon colour. */
.markdown-body .block-tools-btn.is-active {
    border-color: var(--wa-color-brand-border-quiet);
    color: var(--wa-color-brand-on-quiet);
}

/* -- Per-code-block toolbar ------------------------------------------ */
/* The bar sits on the wrapper, never inside the <pre>: the block scrolls
   horizontally, and buttons placed in it would scroll away with the code. */
.markdown-body .code-tools {
    position: relative;
}
/* The wrapper adds no box of its own, so the <pre>'s margins collapse through
   it and keep the block's usual spacing. That also puts the <pre> one level
   below the first/last-child resets above, hence these two: without them, a
   message opening or closing on a code block gains a stray margin. */
.markdown-body > .markdown-block:first-child > .code-tools:first-child > pre {
    margin-top: 0 !important;
}
.markdown-body > .markdown-block:last-child > .code-tools:last-child > pre {
    margin-bottom: 0 !important;
}
/* The raw block stays in the DOM while its rendered view shows, so toggling
   back is instant and the code remains the source for copy. */
.markdown-body .code-tools.is-rendered > pre {
    display: none;
}
/* A frame, so nested content never reads as part of the message around it. */
.markdown-body .code-tools-rendered {
    margin: 1em 0;
    padding: 12px 16px;
    border: 1px solid var(--wa-color-neutral-border-quiet);
    border-radius: 6px;
    background: var(--wa-color-surface-default);
}
.markdown-body .code-tools-rendered > :first-child {
    margin-top: 0;
}
.markdown-body .code-tools-rendered > :last-child {
    margin-bottom: 0;
}

/* -- Per-blockquote toolbar ------------------------------------------ */
/* The bar lives inside the quote — no wrapper: a quote never scrolls, so it
   anchors on the quote itself and the surrounding DOM stays untouched. */
.markdown-body blockquote {
    position: relative;
}
/* github-markdown-css resets the top margin of a quote's first child; the bar
   now holds that slot, so the reset moves to the element after it. Without it, a
   quote opening on a heading gains a stray margin. */
.markdown-body blockquote > .block-tools-bar + * {
    margin-top: 0;
}
/* Only the innermost hovered block shows its bar: a quote inside a quote, or a
   code block opening a quote, would otherwise stack two bars in one corner. */
.markdown-body blockquote:has(blockquote:hover, .code-tools:hover) > .block-tools-bar {
    opacity: 0;
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
/* Defensive: hide any orphan mermaid temp div that escapes into <body>.
   suppressErrorRendering should prevent this, but a regression in mermaid
   or an unrelated failure path would otherwise stack "Syntax error" bombs
   below the app. */
body > div[id^="dmermaid-"] {
    display: none !important;
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

/* -- Search highlight marks (injected by v-highlight directive) ---------- */
mark.search-highlight {
    background-color: oklch(0.85 0.15 90);  /* Warm yellow */
    color: oklch(0.25 0 0);                 /* Dark text for contrast */
    border-radius: 2px;
    padding: 0 1px;
}
html.wa-dark mark.search-highlight {
    background-color: oklch(0.65 0.15 90);  /* Dimmer yellow for dark mode */
    color: oklch(0.95 0 0);                 /* Light text for contrast */
}

/* -- File-path links that don't match any session root render as plain text */
.markdown-body a[data-file-broken] {
    color: inherit;
    text-decoration: none;
    cursor: text;
}

</style>
