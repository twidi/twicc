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
    showAsTree: { type: Boolean, default: false },
    showProviderIcons: { type: Boolean, default: false },
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
                {{ $t('session.list.showArchived') }}
            </wa-dropdown-item>
            <wa-dropdown-item value="archive-older">
                <wa-icon slot="icon" name="box-archive"></wa-icon>
                {{ trimmedSearchQuery ? $t('session.list.archiveOlderFiltered') : $t('session.list.archiveOlder') }}
                <wa-dropdown-item slot="submenu" value="archive-older-24h">{{ $t('session.list.archiveAge.hours24') }}</wa-dropdown-item>
                <wa-dropdown-item slot="submenu" value="archive-older-3d">{{ $t('session.list.archiveAge.days3') }}</wa-dropdown-item>
                <wa-dropdown-item slot="submenu" value="archive-older-7d">{{ $t('session.list.archiveAge.days7') }}</wa-dropdown-item>
                <wa-dropdown-item slot="submenu" value="archive-older-10d">{{ $t('session.list.archiveAge.days10') }}</wa-dropdown-item>
                <wa-dropdown-item slot="submenu" value="archive-older-20d">{{ $t('session.list.archiveAge.days20') }}</wa-dropdown-item>
                <wa-dropdown-item slot="submenu" value="archive-older-30d">{{ $t('session.list.archiveAge.days30') }}</wa-dropdown-item>
                <wa-dropdown-item slot="submenu" value="archive-older-2m">{{ $t('session.list.archiveAge.months2') }}</wa-dropdown-item>
                <wa-dropdown-item slot="submenu" value="archive-older-3m">{{ $t('session.list.archiveAge.months3') }}</wa-dropdown-item>
                <wa-dropdown-item slot="submenu" value="archive-older-6m">{{ $t('session.list.archiveAge.months6') }}</wa-dropdown-item>
            </wa-dropdown-item>
            <wa-dropdown-item
                type="checkbox"
                value="compact-view"
                :checked="compactView"
            >
                {{ $t('session.list.compactView') }}
            </wa-dropdown-item>
            <wa-dropdown-item
                type="checkbox"
                value="show-as-tree"
                :checked="showAsTree"
            >
                {{ $t('session.list.showAsTree') }}
            </wa-dropdown-item>
            <wa-dropdown-item
                type="checkbox"
                value="show-provider-icons"
                :checked="showProviderIcons"
            >
                {{ $t('session.list.showProviderIcons') }}
            </wa-dropdown-item>
            <!-- Multi-select needs Shift/Ctrl modifier clicks, which
                 have no touch equivalent yet — hide it on touch devices. -->
            <wa-dropdown-item
                v-if="!isTouchDevice"
                type="checkbox"
                value="multi-select"
                :checked="multiSelectActive"
            >
                {{ $t('session.list.multiSelect') }}
            </wa-dropdown-item>
            <wa-divider></wa-divider>
            <wa-dropdown-item
                type="checkbox"
                value="show-active"
                :checked="showActiveAcrossFilters"
            >
                <div>{{ $t('session.list.activeAcrossProjects.label') }}</div>
                <div class="session-option-hint">{{ $t('session.list.activeAcrossProjects.hint') }}</div>
            </wa-dropdown-item>
        </wa-dropdown>
        <AppTooltip for="session-options-button">{{ $t('session.list.optionsTooltip') }}</AppTooltip>

        <!-- Search/filter input -->
        <wa-input
            ref="searchInputRef"
            :value="searchQuery"
            @input="emit('update:searchQuery', $event.target.value)"
            :placeholder="$t('session.list.filterPlaceholder')"
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
    /* Enough for the icon alone: a larger minimum overflows the row (and gets cut by the divider)
       once the sidebar is narrower than the options button + this field. */
    min-width: 2.5rem;
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
