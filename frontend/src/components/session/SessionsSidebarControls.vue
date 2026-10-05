<script setup>
// SessionsSidebarControls.vue — the sidebar's second header row in Sessions
// mode: the session-list options dropdown and the filter input. Extracted from ProjectView.vue
// so Artifacts mode can swap in its own controls (ArtifactBookmarksSidebarControls)
// without bolting v-ifs onto session-specific UI.
//
// State stays owned by ProjectView (searchQuery is shared with SessionList);
// this component is a thin view that emits raw events back up:
//  - update:searchQuery  : v-model passthrough for the filter input
//  - optionSelect        : the @wa-select event from the options dropdown,
//                          consumed unchanged by ProjectView.handleSessionOptionsSelect
//  - searchKeydown       : the keydown event from the filter input
// Sessions and Artifacts navigation and full-text search live in the icon bar.
import { ref } from 'vue'
import AppTooltip from '../ui/AppTooltip.vue'

defineProps({
    searchQuery: { type: String, default: '' },
    showArchivedSessions: { type: Boolean, default: false },
    compactView: { type: Boolean, default: false },
    multiSelectActive: { type: Boolean, default: false },
    showActiveAcrossFilters: { type: Boolean, default: false },
    trimmedSearchQuery: { type: String, default: '' },
    isTouchDevice: { type: Boolean, default: false },
})

const emit = defineEmits([
    'update:searchQuery',
    'optionSelect',
    'searchKeydown',
])

const searchInputRef = ref(null)

// All options forward the raw wa-select event unchanged; ProjectView's handler
// reads event.detail.item.
function onOptionSelect(event) {
    emit('optionSelect', event)
}

// ProjectView focuses this control via focusSearchInput (SessionList emits
// @focus-search on ArrowUp from the first item).
function focus() {
    searchInputRef.value?.focus()
}

defineExpose({ focus })
</script>

<template>
    <div class="sidebar-header-row">
        <!-- Session list options dropdown -->
        <wa-dropdown
            placement="bottom-end"
            class="session-options-dropdown"
            @wa-select="onOptionSelect"
        >
            <wa-button
                id="session-options-button"
                slot="trigger"
                variant="brand"
                appearance="outlined"
                size="small"
            >
                <wa-icon name="sliders"></wa-icon>
            </wa-button>
            <wa-dropdown-item
                type="checkbox"
                value="show-archived"
                :checked="showArchivedSessions"
            >
                Show archived sessions
            </wa-dropdown-item>
            <wa-dropdown-item value="archive-older">
                <wa-icon slot="icon" name="box-archive"></wa-icon>
                {{ trimmedSearchQuery ? 'Archive filtered sessions older than…' : 'Archive sessions older than…' }}
                <wa-dropdown-item slot="submenu" value="archive-older-24h">24 hours</wa-dropdown-item>
                <wa-dropdown-item slot="submenu" value="archive-older-3d">3 days</wa-dropdown-item>
                <wa-dropdown-item slot="submenu" value="archive-older-7d">7 days</wa-dropdown-item>
                <wa-dropdown-item slot="submenu" value="archive-older-10d">10 days</wa-dropdown-item>
                <wa-dropdown-item slot="submenu" value="archive-older-20d">20 days</wa-dropdown-item>
                <wa-dropdown-item slot="submenu" value="archive-older-30d">30 days</wa-dropdown-item>
                <wa-dropdown-item slot="submenu" value="archive-older-2m">2 months</wa-dropdown-item>
                <wa-dropdown-item slot="submenu" value="archive-older-3m">3 months</wa-dropdown-item>
                <wa-dropdown-item slot="submenu" value="archive-older-6m">6 months</wa-dropdown-item>
            </wa-dropdown-item>
            <wa-dropdown-item
                type="checkbox"
                value="compact-view"
                :checked="compactView"
            >
                Compact view
            </wa-dropdown-item>
            <!-- Multi-select needs Shift/Ctrl modifier clicks, which
                 have no touch equivalent yet — hide it on touch devices. -->
            <wa-dropdown-item
                v-if="!isTouchDevice"
                type="checkbox"
                value="multi-select"
                :checked="multiSelectActive"
            >
                Multi-select mode
            </wa-dropdown-item>
            <wa-divider></wa-divider>
            <wa-dropdown-item
                type="checkbox"
                value="show-active"
                :checked="showActiveAcrossFilters"
            >
                <div>Show active sessions across projects</div>
                <div class="session-option-hint">All with a running process or unread content</div>
            </wa-dropdown-item>
        </wa-dropdown>
        <AppTooltip for="session-options-button">Session list options</AppTooltip>

        <!-- Search/filter input -->
        <wa-input
            ref="searchInputRef"
            :value="searchQuery"
            @input="emit('update:searchQuery', $event.target.value)"
            placeholder="Filter sessions..."
            size="small"
            with-clear
            class="session-search"
            @keydown="emit('searchKeydown', $event)"
        >
            <wa-icon slot="start" name="magnifying-glass"></wa-icon>
        </wa-input>

    </div>
</template>

<style scoped>
.sidebar-header-row {
    width: 100%;
    display: flex;
    justify-content: stretch;
    align-items: center;
    gap: var(--wa-space-s);
}

.session-search {
    flex: 1;
    min-width: 3.5rem;
    max-width: 100%;
    &:hover {
        z-index: 10;
        min-width: min(10rem, calc(100vw - 100px));
    }
    /* Painted with the canvas (fixed, so pixel-identical to the canvas behind): it reads as
       tinted like the sidebar, yet stays opaque when it widens over its neighbours. */
    &::part(base) {
        background: var(--canvas-background);
        background-attachment: fixed;
    }
    /* Mobile drawer (transformed: `fixed` acts as `scroll`) — see .project-selector-trigger
       in ProjectView.vue. */
    @media (width < 640px) {
        &::part(base) {
            background: linear-gradient(var(--canvas-drawer-top), var(--canvas-drawer-top)), var(--canvas-color);
        }
    }
}

.session-options-dropdown {
    flex-shrink: 0;
    &::part(menu) {
        max-width: 90vw !important;
        width: auto;
    }
}

/* Secondary hint line under a dropdown-item label (same style as the
   "scroll to selected file" subtitle in the files tab root selector). */
.session-option-hint {
    font-size: var(--wa-font-size-xs);
    color: var(--wa-color-text-quiet);
}
</style>
