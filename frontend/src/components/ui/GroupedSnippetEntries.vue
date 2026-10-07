<script setup>
import { onBeforeUnmount, onDeactivated, ref, useId, watch } from 'vue'
import { isSnippetGroup } from '../../utils/snippetGroups'

const props = defineProps({
    entries: { type: Array, default: () => [] },
    context: { default: null },
})

const instanceId = useId()
const openGroup = ref(null)
const popovers = new Map()

function entryKey(entry, index) {
    return isSnippetGroup(entry) ? `${entry._scope || 'global'}:${entry.id}` : index
}

function close() {
    for (const popover of popovers.values()) popover.open = false
    openGroup.value = null
}

function onShow(key) {
    for (const [otherKey, popover] of popovers) {
        if (otherKey !== key) popover.open = false
    }
    openGroup.value = key
}

function setPopover(key, element) {
    if (element) popovers.set(key, element)
    else popovers.delete(key)
}

watch(() => props.context, close, { deep: true })
watch(() => props.entries, close, { deep: true })
onDeactivated(close)
onBeforeUnmount(close)
defineExpose({ close })
</script>

<template>
    <template v-for="(entry, index) in entries" :key="entryKey(entry, index)">
        <template v-if="isSnippetGroup(entry)">
            <slot
                name="group"
                :entry="entry"
                :trigger-id="`${instanceId}-group-${index}`"
                :expanded="openGroup === entryKey(entry, index)"
            />
            <wa-popover
                :ref="element => setPopover(entryKey(entry, index), element)"
                :for="`${instanceId}-group-${index}`"
                placement="top"
                class="snippet-group-popover"
                @wa-show.self="onShow(entryKey(entry, index))"
                @wa-hide.self="openGroup === entryKey(entry, index) && (openGroup = null)"
            >
                <div class="snippet-group-items" :aria-label="entry.label">
                    <slot
                        v-for="(item, itemIndex) in entry.items"
                        :key="itemIndex"
                        :entry="item"
                        :item-id="`${instanceId}-${index}-${itemIndex}`"
                        :close="close"
                    />
                    <span v-if="!entry.items.length" class="empty-group">No items in this group</span>
                </div>
            </wa-popover>
        </template>
        <slot v-else :entry="entry" :item-id="`${instanceId}-${index}`" :close="close" />
    </template>
</template>

<style scoped>
.snippet-group-popover {
    --max-width: min(22rem, calc(100vw - 2rem));
}

.snippet-group-popover::part(body) {
    padding: var(--wa-space-xs);
}

.snippet-group-items {
    display: flex;
    flex-wrap: wrap;
    align-items: center;
    gap: var(--wa-space-2xs);
    max-width: min(22rem, calc(100vw - 3rem));
    max-height: min(18rem, 50dvh);
    overflow-y: auto;
}

.empty-group {
    color: var(--wa-color-text-quiet);
    font-size: var(--wa-font-size-xs);
    padding: var(--wa-space-2xs);
}
</style>
