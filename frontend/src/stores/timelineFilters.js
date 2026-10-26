// frontend/src/stores/timelineFilters.js
// Pinia store for the filters of each Timeline page (filters design section 5).
//
// Ephemeral UI state (never persisted): it lives while the app lives in the browser tab and is lost on a
// reload. One entry per timeline page id (global, each workspace, each project, each worktree), so a page
// shows the same filters when the user comes back to it, whatever happened to its component meanwhile.
//
// Reading never writes: `filtersFor` returns a shared frozen default for a page with no entry, so it is safe
// inside a `computed` or a render. Every action creates the page's entry on its first write.

import { defineStore, acceptHMRUpdate } from 'pinia'
import { AUTHORS, FAMILIES, PROVIDERS, createFilters, toggleValue } from '../utils/timeline/filters.js'

/**
 * The filters of a page that has none yet, shared by every such page. Frozen: assigning one of its fields
 * throws instead of leaking into every other page. The actions never touch it (they write the page's entry).
 */
const DEFAULT_FILTERS = Object.freeze(createFilters())

/** The constant order of each panel group. */
const GROUP_ORDERS = {
    families: FAMILIES.map((family) => family.value),
    providers: PROVIDERS,
    authors: AUTHORS.map((author) => author.value),
}

export const useTimelineFiltersStore = defineStore('timelineFilters', {
    state: () => ({
        /** Filters by timeline page id. A page with no entry has the default filters. */
        byPage: {},
    }),

    actions: {
        /**
         * The filters of a page. Never writes: the shared frozen default when the page has no entry yet.
         *
         * @param {string} pageId
         * @returns {import('../utils/timeline/filters').TimelineFilters}
         */
        filtersFor(pageId) {
            return this.byPage[pageId] ?? DEFAULT_FILTERS
        },

        /** The page's entry, created on the first write. */
        ensure(pageId) {
            if (!this.byPage[pageId]) this.byPage[pageId] = createFilters()
            return this.byPage[pageId]
        },

        /** @param {string} view - One of the `VIEWS` values. */
        setView(pageId, view) {
            this.ensure(pageId).view = view
        },

        /**
         * Check or uncheck one value of a panel group, keeping the group's constant order.
         *
         * @param {'families'|'providers'|'authors'} group
         * @param {string} value
         */
        toggle(pageId, group, value) {
            const filters = this.ensure(pageId)
            filters[group] = toggleValue(filters[group], value, GROUP_ORDERS[group])
        },

        /** @param {'families'|'providers'|'authors'} group */
        clearGroup(pageId, group) {
            this.ensure(pageId)[group] = []
        },

        /** Clear the three panel groups. The view and the session filter stay. */
        resetPanel(pageId) {
            const filters = this.ensure(pageId)
            for (const group of Object.keys(GROUP_ORDERS)) filters[group] = []
        },

        /** @param {{id: string, title: (string|null)}} session */
        setSession(pageId, { id, title }) {
            this.ensure(pageId).session = { id, title }
        },

        clearSession(pageId) {
            this.ensure(pageId).session = null
        },

        /** Clear the panel groups and the session filter, and go back to the `all` view. */
        clearAll(pageId) {
            Object.assign(this.ensure(pageId), createFilters())
        },
    },
})

if (import.meta.hot) {
    import.meta.hot.accept(acceptHMRUpdate(useTimelineFiltersStore, import.meta.hot))
}
