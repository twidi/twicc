import { computed, inject, onActivated, onDeactivated, onMounted, onScopeDispose, ref, toValue } from 'vue'
import { MARKDOWN_RENDER_VIEW_CONTEXT, STREAMING_ROW_CONTEXT } from './streamPublicationKeys.js'

/** Allow initial measurement while suspending work in detached or hidden views. */
export function useMarkdownRenderEligibility() {
    const view = inject(MARKDOWN_RENDER_VIEW_CONTEXT, null)
    const row = inject(STREAMING_ROW_CONTEXT, null)
    const ownerDocument = typeof document === 'undefined' ? null : document
    const mounted = ref(false)
    const attached = ref(true)
    const visible = ref(!ownerDocument || ownerDocument.visibilityState !== 'hidden')
    const updateVisibility = () => { visible.value = ownerDocument.visibilityState !== 'hidden' }

    onMounted(() => {
        ownerDocument?.addEventListener('visibilitychange', updateVisibility)
        if (ownerDocument) updateVisibility()
        mounted.value = true
    })
    onActivated(() => { attached.value = true })
    onDeactivated(() => { attached.value = false })
    onScopeDispose(() => {
        mounted.value = false
        ownerDocument?.removeEventListener('visibilitychange', updateVisibility)
    })

    const eligible = computed(() => mounted.value && attached.value && visible.value
        && (view === null || Boolean(toValue(view)))
        && (row === null || (Boolean(toValue(row.scrollerActive)) && toValue(row.intersection) !== 'outside')))
    return { eligible }
}
