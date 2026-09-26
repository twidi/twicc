import { defineStore } from 'pinia'
import { markRaw } from 'vue'

/**
 * App-level pool of persistent iframes (Browser pane, artifact HTML preview).
 *
 * An <iframe> reloads whenever its DOM node is detached or re-parented — which
 * is exactly what KeepAlive (session switch) and Teleport (dock moves) do to
 * the panes. So the iframes live OUTSIDE the pane subtree: FrameHost (mounted
 * once in ProjectView's .main-content) renders every registered frame,
 * absolutely positioned over its pane's placeholder rect (PersistentFrame
 * tracks it). The placeholder moves freely; the iframe node never does.
 *
 * Same philosophy as stores/terminalPool.js, different payload constraint:
 * terminals tolerate <Teleport> relocation, iframes need rect sync instead.
 *
 * HARD INVARIANT — append-only key order. FrameHost renders `frames` with a
 * keyed v-for in Object.keys() order; reordering keys would move iframe DOM
 * nodes and reload them. Insertions append, deletions don't shift siblings;
 * never sort, never rebuild the object.
 */
export const useFramePoolStore = defineStore('framePool', {
    state: () => ({
        // id → descriptor {
        //   src, remountKey, attrs, zTier ('base'|'overlay'|'fullscreen'),
        //   visible, rect {x,y,width,height},
        //   clipRect ({x,y,width,height}|null) — viewport rect of the owner's
        //     scroll container; FrameHost clip-paths the frame to it so a
        //     scrolled-out placeholder doesn't paint over the pane's chrome,
        //   cardRect ({x,y,width,height}|null) — viewport rect of the .panel-card
        //     containing the placeholder; FrameHost rounds the frame corners that are
        //     flush with it (utils/panelInsets.js),
        //   onLoad (markRaw fn|null),
        //   el (markRaw iframe element|null, set by FrameHost),
        //   overlayEl (markRaw div|null, set by FrameHost),
        // }
        frames: {},
        // FrameHost presence — PersistentFrame falls back to an inline iframe
        // (today's behavior) when no host is mounted. Written by PROJECTVIEW's
        // setup/onUnmounted (not FrameHost): panes can mount before FrameHost's
        // DOM on cold-load deep links, and the flag must already be true.
        hostMounted: false,
        // Bumped by layout owners after mutations that can move a placeholder
        // without resizing it (dock retarget, overlay open/close, maximize):
        // ResizeObserver is blind to position-only changes.
        geometryEpoch: 0,
        // >0 while a split/dock divider drag is in progress → FrameHost turns
        // off pointer-events on iframes so the drag keeps tracking.
        dividerDragDepth: 0,
    }),

    getters: {
        isDividerDragging: (state) => state.dividerDragDepth > 0,
        frameEl: (state) => (id) => state.frames[id]?.el || null,
        frameOverlayEl: (state) => (id) => state.frames[id]?.overlayEl || null,
    },

    actions: {
        register(id, { src, remountKey, attrs, zTier, onLoad }) {
            this.frames[id] = {
                src,
                remountKey,
                attrs: attrs || {},
                zTier: zTier || 'base',
                visible: false,
                rect: { x: 0, y: 0, width: 0, height: 0 },
                clipRect: null,
                cardRect: null,
                onLoad: onLoad ? markRaw(onLoad) : null,
                el: null,
                overlayEl: null,
            }
        },

        patch(id, fields) {
            const frame = this.frames[id]
            if (frame) Object.assign(frame, fields)
        },

        setRect(id, rect) {
            const frame = this.frames[id]
            if (frame) frame.rect = rect
        },

        // Separate setters: Vue invokes a function ref synchronously when ITS
        // vnode mounts, before the next sibling is patched — at the iframe's
        // first ref call the overlay div doesn't exist yet, so a single
        // "el + nextElementSibling" setter would record overlayEl = null.
        setFrameEl(id, el) {
            const frame = this.frames[id]
            if (frame) frame.el = el ? markRaw(el) : null
        },

        setOverlayEl(id, el) {
            const frame = this.frames[id]
            if (frame) frame.overlayEl = el ? markRaw(el) : null
        },

        unregister(id) {
            delete this.frames[id]
        },

        setHostMounted(mounted) {
            this.hostMounted = mounted
        },

        bumpGeometry() {
            this.geometryEpoch++
        },

        beginDividerDrag() {
            this.dividerDragDepth++
        },

        endDividerDrag() {
            if (this.dividerDragDepth > 0) this.dividerDragDepth--
        },
    },
})
