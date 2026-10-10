// Shared Mermaid rendering: lazy-load + per-diagram theme injection.
//
// Used by MarkdownContent (markdown-embedded ```mermaid blocks) and by the
// file preview (standalone .mmd / .mermaid files in the Files / Artifacts tabs).

// Lazy-loaded mermaid instance (dynamic import to avoid ~500KB in main bundle).
let mermaidModule = null
let mermaidInitialized = false

export async function getMermaid() {
    if (!mermaidModule) {
        mermaidModule = (await import('mermaid')).default
    }
    if (!mermaidInitialized) {
        mermaidInitialized = true
        mermaidModule.initialize({
            startOnLoad: false,
            theme: mermaidThemeFor('light'),
            // Mermaid 12's defaults, pinned so a future default change does not
            // silently restyle every diagram: the `neo` look, and the ELK layout
            // (orthogonal edges, tidier subgraphs than dagre).
            look: 'neo',
            layout: 'elk',
            securityLevel: 'loose',
            // Don't inject the "Syntax error" bomb diagram on parse/render
            // failures: during streaming, the markdown is re-rendered repeatedly
            // with an incomplete mermaid block, and mermaid would otherwise leave
            // one orphan div per failed attempt in <body>, piling up below the app.
            suppressErrorRendering: true,
        })
    }
    return mermaidModule
}

// Detect whether a mermaid source already pins a theme — via a `theme:` key in
// YAML front-matter (--- ... ---) or a `theme` field inside an %%{init: ...}%%
// directive. When it does, the author's choice is left untouched (same diagram
// in both light and dark).
export function sourceHasOwnTheme(source) {
    const fm = source.match(/^\s*---\r?\n([\s\S]*?)\r?\n---/)
    if (fm && /\btheme\s*:/.test(fm[1])) return true
    return /%%\{\s*init\s*:[\s\S]*?\btheme\b/i.test(source)
}

// The native mermaid theme for TwiCC's effective color scheme. The redux
// themes are neutral; their `-color` variants keep subgraph backgrounds
// visible in both modes.
export function mermaidThemeFor(colorScheme) {
    return colorScheme === 'dark' ? 'redux-dark-color' : 'redux-color'
}

// Make a mermaid source render with the given native theme (see
// mermaidThemeFor) by injecting an %%{init: {'theme': ...}}%% directive. The
// directive overrides the global mermaid config per-diagram, so rendering is
// stateless (no global re-init race between concurrently rendering blocks) and
// the SVG is fully determined by (source, theme). Front-matter must stay at the
// very start of the source, so when present the directive is inserted right
// after it; otherwise it is prepended.
export function applyMermaidTheme(source, theme) {
    if (sourceHasOwnTheme(source)) return source
    const directive = `%%{init: {'theme': '${theme}'}}%%\n`
    const fm = source.match(/^\s*---\r?\n[\s\S]*?\r?\n---[ \t]*\r?\n?/)
    if (fm) return source.slice(0, fm[0].length) + directive + source.slice(fm[0].length)
    return directive + source
}

// Render a mermaid source to an SVG string for the given native theme. Throws if
// mermaid fails to parse/render (the caller decides how to surface the error).
export async function renderMermaidToSvg(source, theme, { id } = {}) {
    const mermaid = await getMermaid()
    const renderId = id || `twicc-mermaid-${Math.random().toString(36).slice(2, 11)}`
    const { svg } = await mermaid.render(renderId, applyMermaidTheme(source, theme))
    return svg
}
