<script setup>
/**
 * LazyRowMenu - A list row's "⋮" actions menu, mounted on first use.
 *
 * A closed row renders only the trigger button. The first click (mouse, Enter or
 * Space) mounts the wa-dropdown with its items, then opens it. The dropdown then
 * stays mounted until the row unmounts: going back to the bare button would destroy
 * the element that holds the focus. Lists mount many rows and open few menus.
 *
 * Usage:
 *   <LazyRowMenu :trigger-id="id" label="Session actions" menu-class="session-menu"
 *                trigger-class="session-menu-trigger" @select="handleMenuSelect">
 *       <wa-dropdown-item value="rename">Rename</wa-dropdown-item>
 *   </LazyRowMenu>
 *
 * Props:
 *   - triggerId: id of the trigger button, the tooltip's anchor.
 *   - label: accessible label of the trigger icon, also the tooltip text.
 *   - menuClass / triggerClass: the list's own classes, next to the shared
 *     sidebar-row-menu / sidebar-row-menu-trigger (styles/sidebar-rows.css).
 *   - placement: the dropdown placement.
 *   - hoistTooltip: forwarded to the tooltip's `hoist`.
 *   - deferredTooltip: forwarded to the tooltip's `deferred` (AppTooltip).
 *
 * Emits `select` with the wa-select event.
 */
import { nextTick, ref } from 'vue'
import AppTooltip from './AppTooltip.vue'

defineProps({
    triggerId: { type: String, required: true },
    label: { type: String, required: true },
    menuClass: { type: String, default: '' },
    triggerClass: { type: String, default: '' },
    placement: { type: String, default: 'bottom-end' },
    hoistTooltip: { type: Boolean, default: false },
    deferredTooltip: { type: Boolean, default: false },
})

const emit = defineEmits(['select'])

const menuMounted = ref(false)
const dropdownRef = ref(null)

async function mountMenu() {
    menuMounted.value = true
    await nextTick()
    // Opened only after its first render: a wa-dropdown rendered open already has an
    // active popup, so its showMenu() returns early and skips the outside-click and
    // Escape listeners and the first-item focus.
    await dropdownRef.value?.updateComplete
    if (dropdownRef.value) dropdownRef.value.open = true
}
</script>

<template>
    <wa-dropdown
        v-if="menuMounted"
        ref="dropdownRef"
        class="sidebar-row-menu"
        :class="menuClass"
        :placement="placement"
        @wa-select="emit('select', $event)"
    >
        <wa-button
            :id="triggerId"
            slot="trigger"
            variant="neutral"
            appearance="plain"
            size="small"
            class="sidebar-row-menu-trigger"
            :class="triggerClass"
        >
            <wa-icon name="ellipsis-v" :label="label"></wa-icon>
        </wa-button>
        <slot />
    </wa-dropdown>
    <span v-else class="sidebar-row-menu" :class="menuClass">
        <wa-button
            :id="triggerId"
            variant="neutral"
            appearance="plain"
            size="small"
            class="sidebar-row-menu-trigger"
            :class="triggerClass"
            @click="mountMenu"
        >
            <wa-icon name="ellipsis-v" :label="label"></wa-icon>
        </wa-button>
    </span>
    <!-- Keyed on the mount: wa-tooltip resolves its `for` anchor only when it connects,
         and the mount replaces the trigger button. -->
    <AppTooltip :key="menuMounted" :deferred="deferredTooltip" :hoist="hoistTooltip" :for="triggerId">{{ label }}</AppTooltip>
</template>
