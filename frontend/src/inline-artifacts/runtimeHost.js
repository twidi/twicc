/** Stable view chrome owns events, independently of virtual transcript rows. */
export function installInlineRuntimeHost({ runtime, window, expandPreviewHost }) {
    let expanded = false, disposed = false
    const schedule = () => runtime.geometry.schedule()
    const keydown = event => {
        if (event.key !== 'Escape' || !runtime.fullscreenArtifactKey.value) return
        event.stopPropagation()
        runtime.closeFullscreen()
    }
    window.addEventListener('scroll', schedule, true)
    window.addEventListener('resize', schedule)
    function syncFullscreen() {
        if (disposed) return
        const value = !!runtime.fullscreenArtifactKey.value
        if (value !== expanded) {
            expanded = value
            expandPreviewHost?.(value)
            if (value) window.addEventListener('keydown', keydown, true)
            else window.removeEventListener('keydown', keydown, true)
        }
        schedule()
    }
    function dispose() {
        if (disposed) return
        disposed = true
        window.removeEventListener('scroll', schedule, true)
        window.removeEventListener('resize', schedule)
        window.removeEventListener('keydown', keydown, true)
        if (expanded) expandPreviewHost?.(false)
    }
    return { syncFullscreen, dispose }
}
