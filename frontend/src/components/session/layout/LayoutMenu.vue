<script setup>
// The overflow (⋮) menu in the main-area tab nav, combining both layout actions:
//   • Save layout   (only when not single pane — there must be something to save)
//   • Select layout (a disabled header), then Single pane + the named catalog
// Lives in the tab nav, so every WA custom event is stopped from bubbling to the tab group.
import { computed } from 'vue'
import { useLayoutsStore, SINGLE_PANE_ID } from '../../../stores/layouts'

const props = defineProps({
    // Whether there is a layout to save (not single pane). Drives the "Save layout" entry.
    hasDocks: { type: Boolean, default: false },
    // Resolved scope defaults to surface as their own rows: [{ scope, label, layoutId, layoutName }].
    // Each points at a real named layout; the named-layouts list below excludes these ids (no dup).
    scopeDefaults: { type: Array, default: () => [] },
})

const emit = defineEmits(['save', 'select', 'manage'])

// Uppercase sentinels for the action items — layout ids are always lowercased slugs, so they can
// never collide with a named layout's id.
const SAVE_VALUE = '__SAVE__'
const MANAGE_VALUE = '__MANAGE__'

const layoutsStore = useLayoutsStore()
// The whole catalog drives whether "Manage layouts…" shows (even if every layout is a scope default).
const hasCatalog = computed(() => layoutsStore.getAllLayouts.length > 0)
// Named layouts not already surfaced as a scope-default row (so a layout never appears twice).
const scopeDefaultIds = computed(() => new Set(props.scopeDefaults.map((d) => d.layoutId)))
const namedLayouts = computed(() => layoutsStore.getAllLayouts.filter((l) => !scopeDefaultIds.value.has(l.id)))

function onSelect(event) {
    const value = event.detail?.item?.value
    if (!value) return
    if (value === SAVE_VALUE) emit('save')
    else if (value === MANAGE_VALUE) emit('manage')
    else emit('select', value)
}
</script>

<template>
    <wa-dropdown
        class="layout-menu"
        placement="bottom-end"
        @click.stop
        @wa-select.stop="onSelect"
        @wa-show.stop
        @wa-hide.stop
        @wa-after-show.stop
        @wa-after-hide.stop
    >
        <wa-button
            slot="trigger"
            class="layout-menu-trigger reduced-height"
            appearance="plain"
            size="small"
            :title="$t('session.layout.label')"
            :aria-label="$t('session.layout.menuLabel')"
        >
            <wa-icon name="ellipsis-v"></wa-icon>
        </wa-button>

        <template v-if="props.hasDocks">
            <wa-dropdown-item :value="SAVE_VALUE">{{ $t('session.layout.save.title') }}</wa-dropdown-item>
            <wa-divider></wa-divider>
        </template>

        <wa-dropdown-item disabled class="layout-menu-header">{{ $t('session.layout.select') }}</wa-dropdown-item>
        <wa-dropdown-item :value="SINGLE_PANE_ID">{{ $t('session.layout.singlePane') }}</wa-dropdown-item>

        <!-- Scope defaults (worktree → project → global): a shortcut to each scope's default layout. -->
        <template v-if="scopeDefaults.length">
            <wa-divider></wa-divider>
            <wa-dropdown-item v-for="d in scopeDefaults" :key="d.scope" :value="d.layoutId">
                {{ d.label }} — {{ d.layoutName }}
            </wa-dropdown-item>
        </template>

        <!-- The remaining named layouts (alphabetical), minus any already shown as a scope default. -->
        <template v-if="namedLayouts.length">
            <wa-divider></wa-divider>
            <wa-dropdown-item v-for="l in namedLayouts" :key="l.id" :value="l.id">{{ l.name }}</wa-dropdown-item>
        </template>

        <template v-if="hasCatalog">
            <wa-divider></wa-divider>
            <wa-dropdown-item :value="MANAGE_VALUE">
                <wa-icon slot="icon" name="sliders"></wa-icon>
                {{ $t('settings.layouts.manage') }}
            </wa-dropdown-item>
        </template>
    </wa-dropdown>
</template>

<style scoped>
.layout-menu-trigger {
    --wa-form-control-padding-inline: 0.3em;
}
</style>
