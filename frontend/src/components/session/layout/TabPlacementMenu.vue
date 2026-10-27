<script setup>
// Small per-tab arrow opening a dropdown to place the tab into the center or one of the six
// docks. Menu only (no drag-and-drop). Lives inside a wa-tab / nav, so every WA event is
// stopped from bubbling to the surrounding tab group, and clicks never switch the tab.
import { computed } from 'vue'
import { PLACEMENT_OPTIONS, DOCK_LABELS, DOCK_ICONS, MERGED_PLACEMENTS, edgeOfDock } from './dockMeta'

const props = defineProps({
    tabId: { type: String, required: true },
    current: { type: String, default: 'center' }, // dockId or 'center'
    isDockOccupied: { type: Function, required: true }, // dockId -> whether it holds a tab
})
const emit = defineEmits(['place'])

// Menu entries. While an edge holds nothing, its two docks collapse into one entry placing into
// the first dock of the edge, so newcomers do not face a top/bottom choice with no visible difference.
const options = computed(() => {
    const result = []
    const seenEdges = new Set()
    for (const dockId of PLACEMENT_OPTIONS) {
        const edge = edgeOfDock(dockId)
        const merged = edge && MERGED_PLACEMENTS[edge]
        const edgeEmpty = merged
            && !PLACEMENT_OPTIONS.some((d) => edgeOfDock(d) === edge && props.isDockOccupied(d))
        if (!edgeEmpty) {
            result.push({ value: dockId, label: DOCK_LABELS[dockId], icon: DOCK_ICONS[dockId] })
        } else if (!seenEdges.has(edge)) {
            seenEdges.add(edge)
            result.push({ value: merged.dockId, label: merged.label, icon: merged.icon })
        }
    }
    return result
})

function onSelect(event) {
    const dest = event.detail?.item?.value
    if (dest) emit('place', event.detail.item.value)
}
</script>

<template>
    <wa-dropdown
        class="placement-menu"
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
            appearance="plain"
            size="small"
            class="placement-trigger"
            title="Place this tab"
            aria-label="Place this tab"
        >
            <wa-icon name="chevron-down"></wa-icon>
        </wa-button>

        <wa-dropdown-item disabled class="placement-header">Tab placement</wa-dropdown-item>
        <wa-divider></wa-divider>
        <wa-dropdown-item
            v-for="opt in options"
            :key="opt.value"
            type="checkbox"
            :value="opt.value"
            :checked="opt.value === current"
        >
            <wa-icon slot="icon" :src="opt.icon"></wa-icon>
            {{ opt.label }}
        </wa-dropdown-item>
    </wa-dropdown>
</template>

<style scoped>
.placement-menu {
    display: inline-flex;
}
.placement-trigger {
    --wa-form-control-padding-inline: 0.25em;
    font-size: 0.5em;
    opacity: 0.6;
}
.placement-trigger:hover {
    opacity: 1;
}
</style>
