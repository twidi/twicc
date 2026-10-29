import { ref, watch, onScopeDispose, onDeactivated } from 'vue'

// Only the open toolbar owns document listeners. Closed messages add none.
let closeActiveToolbar = null

export function useMarkdownToolbar(toolbar, toggleButton) {
    const opened = ref(false)

    function close() {
        opened.value = false
        if (closeActiveToolbar === close) closeActiveToolbar = null
    }

    function toggle() {
        if (opened.value) return close()
        closeActiveToolbar?.()
        closeActiveToolbar = close
        opened.value = true
    }

    // WA does not forward aria-expanded from its host to the native button.
    watch([toggleButton, opened], async () => {
        const button = toggleButton.value
        await button?.updateComplete
        if (toggleButton.value !== button) return
        button?.shadowRoot?.querySelector('button')?.setAttribute('aria-expanded', String(opened.value))
    }, { flush: 'post' })

    watch(opened, (isOpen, _previous, onCleanup) => {
        const document = toolbar.value?.ownerDocument
        if (!isOpen || !document) return

        const closeOutside = event => {
            if (!event.composedPath().includes(toolbar.value)) close()
        }
        const onKeydown = event => {
            if (event.key !== 'Escape') return
            event.preventDefault()
            event.stopPropagation()
            close()
            toggleButton.value?.focus()
        }
        document.addEventListener('pointerdown', closeOutside, true)
        document.addEventListener('focusin', closeOutside, true)
        document.addEventListener('keydown', onKeydown, true)
        onCleanup(() => {
            document.removeEventListener('pointerdown', closeOutside, true)
            document.removeEventListener('focusin', closeOutside, true)
            document.removeEventListener('keydown', onKeydown, true)
        })
    }, { flush: 'sync' })

    onDeactivated(close)
    onScopeDispose(close)
    return { opened, toggle, close }
}
