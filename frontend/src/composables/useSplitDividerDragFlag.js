import { onBeforeUnmount, onMounted, ref } from 'vue'
import { useFramePoolStore } from '../stores/framePool'

/**
 * Flag divider drags of a <wa-split-panel> into the frame pool so FrameHost
 * can neutralize iframe pointer-events for the duration (an iframe would
 * otherwise capture pointermove and freeze the drag). The docking gutters
 * have their own wiring in SessionLayout.vue; this covers the three plain
 * wa-split-panels (project sidebar, FilesPanel tree/content, GitPanel
 * tree/content) whose drags over an iframe are broken today already.
 *
 * Also returns the drag state as a ref (`dragging`): wa-split-panel exposes
 * none, and the project sidebar divider shows its line while dragged.
 */
export function useSplitDividerDragFlag(splitPanelRef) {
    const pool = useFramePoolStore()
    const dragging = ref(false)

    function onPointerDown(event) {
        // THIS panel's own divider only (WA exposes it as the `divider` property). A
        // part="divider" test would also match a nested split panel's divider bubbling up
        // through this host (e.g. the Files/Git tree splitters inside the project content).
        const divider = splitPanelRef.value?.divider
        if (!divider || !event.composedPath().includes(divider)) return
        dragging.value = true
        pool.beginDividerDrag()
    }

    function onPointerEnd() {
        if (!dragging.value) return
        dragging.value = false
        pool.endDividerDrag()
    }

    onMounted(() => {
        splitPanelRef.value?.addEventListener('pointerdown', onPointerDown)
        window.addEventListener('pointerup', onPointerEnd, true)
        window.addEventListener('pointercancel', onPointerEnd, true)
    })
    onBeforeUnmount(() => {
        splitPanelRef.value?.removeEventListener('pointerdown', onPointerDown)
        window.removeEventListener('pointerup', onPointerEnd, true)
        window.removeEventListener('pointercancel', onPointerEnd, true)
        onPointerEnd() // never leave the depth stuck if unmounted mid-drag
    })

    return { dragging }
}
