import { INLINE_ARTIFACT_RELOAD_QUERY } from './context.js'
import { createInlineScrollForwarder } from './scrollForwarder.js'

/** Ignore body/root height: they often have a minimum of the iframe viewport. */
export function intrinsicInlineHeight(document, window) {
    const body = document.body
    if (!body) return null
    const top = body.getBoundingClientRect().top
    let bottom = top
    for (const child of body.children) {
        if (['SCRIPT', 'STYLE', 'LINK'].includes(child.tagName)) continue
        const style = window.getComputedStyle(child)
        bottom = Math.max(bottom, child.getBoundingClientRect().bottom + (parseFloat(style.marginBottom) || 0))
    }
    // Text-only documents have no element box to measure.
    for (const node of body.childNodes) {
        if (node.nodeType !== 3 || !node.textContent.trim()) continue
        const range = document.createRange()
        range.selectNode(node)
        bottom = Math.max(bottom, range.getBoundingClientRect().bottom)
    }
    const style = window.getComputedStyle(body)
    return Math.ceil(bottom - Math.min(0, top) + (parseFloat(style.marginBottom) || 0))
}

/** Bound document observation; rapid feedback falls back to internal scrolling. */
export function createInlineHeightObserver({ document, window, getHost,
    requestFrame = callback => window.requestAnimationFrame(callback),
    cancelFrame = id => window.cancelAnimationFrame(id),
    ResizeObserver = window.ResizeObserver, MutationObserver = window.MutationObserver,
    now = () => Date.now(),
}) {
    const token = new URL(window.location.href).searchParams.get(INLINE_ARTIFACT_RELOAD_QUERY)
    const inlineGeneration = token && /^\d+$/.test(token) ? Number(token) : null
    let pending = null, disposed = false, disabled = false, lastHeight = null, lastMode = null
    let burstStart = 0, changes = 0
    let resizeObserver, mutationObserver
    async function measure() {
        pending = null
        if (disposed || disabled || inlineGeneration == null) return
        try {
            const host = await getHost()
            const state = await host.getInlineState({ inlineGeneration })
            if (disposed || disabled || !state || state.inlineGeneration !== inlineGeneration) return
            // Never feed fullscreen dimensions back into the remembered inline box.
            if (state.mode !== 'inline') { lastMode = state.mode; return }
            const height = intrinsicInlineHeight(document, window)
            if (!Number.isFinite(height) || height === lastHeight && lastMode === state.mode) return
            const time = now()
            if (time - burstStart > 1000) { burstStart = time; changes = 0 }
            changes++
            lastMode = state.mode
            if (changes > 8) {
                disabled = true
                resizeObserver?.disconnect()
                mutationObserver?.disconnect()
                await host.reportInlineHeight({ inlineGeneration, mode: 'inline', height: state.requestedHeight })
                return
            }
            lastHeight = height
            await host.reportInlineHeight({ inlineGeneration, mode: state.mode, height })
        } catch { /* Optional reporting cannot interrupt an artifact. */ }
    }
    function schedule() {
        if (!disposed && !disabled && pending == null && inlineGeneration != null) pending = requestFrame(measure)
    }
    const observedChildren = new Set()
    function observeChildren() {
        const current = new Set(document.body?.children || [])
        for (const child of observedChildren) {
            if (!current.has(child)) { resizeObserver?.unobserve?.(child); observedChildren.delete(child) }
        }
        for (const child of current) {
            if (!observedChildren.has(child)) { resizeObserver?.observe(child); observedChildren.add(child) }
        }
    }
    function observe() {
        if (disposed || inlineGeneration == null) return
        resizeObserver = ResizeObserver ? new ResizeObserver(schedule) : null
        resizeObserver?.observe(document.documentElement)
        if (document.body) resizeObserver?.observe(document.body)
        observeChildren()
        mutationObserver = MutationObserver ? new MutationObserver(() => { observeChildren(); schedule() }) : null
        mutationObserver?.observe(document.documentElement, { childList: true, subtree: true, characterData: true, attributes: true })
        schedule()
    }
    function keydown(event) {
        if (event.key !== 'Escape' || inlineGeneration == null) return
        getHost().then(host => { if (!disposed) return host.requestInlineEscape({ inlineGeneration }) }).catch(() => {})
    }
    const scrollForwarder = createInlineScrollForwarder({ document, window, getHost, inlineGeneration })
    window.addEventListener('keydown', keydown, true)
    window.addEventListener('resize', schedule)
    if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', observe, { once: true })
    else observe()
    getHost().then(host => { if (!disposed) return host.reportInlineReady({ inlineGeneration }) }).catch(() => {})
    function dispose() {
        if (disposed) return
        disposed = true
        if (pending != null) cancelFrame(pending)
        resizeObserver?.disconnect(); mutationObserver?.disconnect()
        observedChildren.clear()
        scrollForwarder.dispose()
        window.removeEventListener('keydown', keydown, true)
        window.removeEventListener('resize', schedule)
        document.removeEventListener('DOMContentLoaded', observe)
    }
    return { schedule, dispose }
}
