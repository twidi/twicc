import { i18n } from '../i18n/index.js'
import { defineStore } from 'pinia'

// The synthetic "no docks" layout. Injected in code (never stored in layouts.json), always present
// in every selector, not editable / overwritable / deletable. Its intention is the empty one.
export const SINGLE_PANE_ID = 'single-pane'
export const SINGLE_PANE_NAME = 'Single pane'

// Keep only the template keys a named layout carries (the structure). Runtime fields
// (activeByGroup / activeSide / activeResize) and the transient `maximized` are session-only and
// never stored in the catalog.
function sanitizeIntention(intention) {
    const i = intention && typeof intention === 'object' ? intention : {}
    return {
        assignment: { ...(i.assignment || {}) },
        collapsed: Array.isArray(i.collapsed) ? [...i.collapsed] : [],
        resizeFractions: { ...(i.resizeFractions || {}) },
        tabOrder: Array.isArray(i.tabOrder) ? [...new Set(i.tabOrder.filter((id) => typeof id === 'string'))] : [],
    }
}

// Display order for layouts everywhere they are listed (menus, selectors, manager): alphabetical by
// name. The stored `state.layouts` keeps insertion order (mutations use it directly); only the
// display getters sort.
function sortByName(layouts) {
    return [...layouts].sort((a, b) => a.name.localeCompare(b.name))
}

export const useLayoutsStore = defineStore('layouts', {
    state: () => ({
        layouts: [],              // Array of { id, name, intention: { assignment, collapsed, resizeFractions, tabOrder } }
        _isApplyingRemote: false, // Guard to prevent echo on WS receive
    }),

    getters: {
        /** All named layouts, alphabetical by name (display order). */
        getAllLayouts: (state) => sortByName(state.layouts),

        /** A named layout by id (the synthetic single pane is NOT in here). */
        getLayoutById: (state) => (id) => state.layouts.find((l) => l.id === id) || null,

        /** Everything selectable: the synthetic single pane first, then the named layouts (alphabetical).
         *  Drives every layout picker (session selector, project/global default pickers). */
        selectableLayouts: (state) => [
            { id: SINGLE_PANE_ID, name: i18n.global.t('session.layout.singlePane'), synthetic: true },
            ...sortByName(state.layouts),
        ],

        /** Resolve an id to a (deep-cloned) intention. ``single-pane`` and any unknown / dangling id
         *  → the empty intention (single pane), so a deleted-but-still-referenced layout degrades
         *  safely instead of breaking. */
        intentionForId: (state) => (id) => {
            if (!id || id === SINGLE_PANE_ID) return {}
            const found = state.layouts.find((l) => l.id === id)
            return found ? structuredClone(sanitizeIntention(found.intention)) : {}
        },
    },

    actions: {
        /** Apply the catalog received from the backend (WS: connect push / another client's update). */
        applyLayouts(layouts) {
            this._isApplyingRemote = true
            this.layouts = (layouts || []).map((l) => ({
                id: l.id,
                name: l.name,
                intention: sanitizeIntention(l.intention),
            }))
            this._isApplyingRemote = false
        },

        /** Persist the whole catalog to the backend (which broadcasts it back). */
        async _sendLayouts() {
            if (this._isApplyingRemote) return
            const { sendLayouts } = await import('../composables/useWebSocket')
            sendLayouts(this.layouts)
        },

        /** Slug id from a name (mirror of workspaces' _generateId); the synthetic ``single-pane`` id
         *  is reserved, so a named layout can never collide with it. */
        _generateId(name) {
            let base = name
                .toLowerCase()
                .replace(/[^a-z0-9_-]/g, '-')
                .replace(/-{2,}/g, '-')
                .replace(/^-|-$/g, '')
            if (!base) base = 'layout'
            const existingIds = new Set(this.layouts.map((l) => l.id))
            existingIds.add(SINGLE_PANE_ID)
            if (!existingIds.has(base)) return base
            let suffix = 2
            while (existingIds.has(`${base}-${suffix}`)) suffix++
            return `${base}-${suffix}`
        },

        /** Create a new named layout (id omitted) or overwrite an existing one (id given). Returns the
         *  layout's id. The intention is sanitized to the template subset before storing. */
        upsertLayout({ id = null, name, intention }) {
            const trimmed = (name || '').trim()
            if (!trimmed) return null
            const clean = sanitizeIntention(intention)
            if (id && id !== SINGLE_PANE_ID) {
                const existing = this.layouts.find((l) => l.id === id)
                if (existing) {
                    existing.name = trimmed
                    existing.intention = clean
                    this._sendLayouts()
                    return id
                }
            }
            const newId = this._generateId(trimmed)
            this.layouts.push({ id: newId, name: trimmed, intention: clean })
            this._sendLayouts()
            return newId
        },

        /** Rename a named layout (id-based; references stay valid). */
        renameLayout(id, name) {
            const layout = this.layouts.find((l) => l.id === id)
            const trimmed = (name || '').trim()
            if (!layout || !trimmed) return
            layout.name = trimmed
            this._sendLayouts()
        },

        /** Delete a named layout. References to it degrade to single pane at resolution time
         *  (intentionForId is dangling-tolerant); reassignment of scope defaults is handled by the
         *  caller (the manager dialog) in a later step. */
        deleteLayout(id) {
            const index = this.layouts.findIndex((l) => l.id === id)
            if (index === -1) return
            this.layouts.splice(index, 1)
            this._sendLayouts()
        },
    },
})
