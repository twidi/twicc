import { inject, onActivated, onDeactivated, onScopeDispose, ref, toValue, watch } from 'vue'
import { streamPublicationRegistry } from '../utils/streamPublicationRegistry.js'
import { STREAMING_VIEW_CONTEXT, STREAMING_ROW_CONTEXT } from './streamPublicationKeys.js'

export function useStreamingPublication({ identity, bodyActive }) {
    const view = inject(STREAMING_VIEW_CONTEXT, null)
    const row = inject(STREAMING_ROW_CONTEXT, null)
    const attached = ref(true)
    let token = null, ownedIdentity = null
    function release() {
        if (token) streamPublicationRegistry.release(token)
        token = null
        ownedIdentity = null
    }
    watch(() => [toValue(identity), toValue(bodyActive), toValue(view),
        toValue(row?.intersection), toValue(row?.scrollerActive), attached.value],
    ([current, body, active, intersection, scrollerActive, attached]) => {
        if (!current || !body || !active || !scrollerActive || !attached) { release(); return }
        const state = { viewActive: true, bodyActive: true, intersection }
        if (ownedIdentity !== current) {
            release()
            ownedIdentity = current
            token = streamPublicationRegistry.acquire(current, state)
        } else streamPublicationRegistry.update(token, state)
    }, { immediate: true, flush: 'sync' })
    onDeactivated(() => { attached.value = false; release() })
    onActivated(() => { attached.value = true })
    onScopeDispose(release)
}
