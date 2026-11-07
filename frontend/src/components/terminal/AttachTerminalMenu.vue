<script setup>
// Chevron-down menu in the terminal tab bar: "Attach terminal from".
// Lists, grouped by ancestor scope (worktree → project → workspace(s) → global),
// the terminals of the parent levels that can be attached into this panel.
// Presentational only: the parent (TerminalPanel) resolves the sections from the
// terminalTabs store and performs the actual attach. Lives in the tab bar, so —
// like LayoutMenu — every WA custom event is stopped from bubbling to the tab group.
import { computed } from 'vue'

const props = defineProps({
    // Ancestor sections: [{ scope, label, items: [{ key, contextKey, index, label, attached }] }].
    // Only scopes that have at least one terminal are included by the parent.
    sections: { type: Array, default: () => [] },
})

const emit = defineEmits(['attach', 'open'])

// Flat key → item lookup so a selection resolves back to its descriptor.
const itemsByKey = computed(() => {
    const map = new Map()
    for (const section of props.sections) {
        for (const item of section.items) map.set(item.key, item)
    }
    return map
})

const hasItems = computed(() => props.sections.some(s => s.items.length > 0))

function onSelect(event) {
    const value = event.detail?.item?.value
    if (!value) return
    const item = itemsByKey.value.get(value)
    if (item && !item.attached) emit('attach', item)
}
</script>

<template>
    <wa-dropdown
        class="attach-terminal-menu"
        placement="bottom-end"
        @click.stop
        @wa-select.stop="onSelect"
        @wa-show.stop="emit('open')"
        @wa-hide.stop
        @wa-after-show.stop
        @wa-after-hide.stop
    >
        <wa-button
            slot="trigger"
            class="attach-terminal-trigger reduced-height"
            appearance="plain"
            size="small"
            :title="$t('terminal.attach.title')"
            :aria-label="$t('terminal.attach.label')"
        >
            <wa-icon name="link"></wa-icon>
            <wa-icon name="chevron-down"></wa-icon>
        </wa-button>

        <wa-dropdown-item disabled class="attach-terminal-header">{{ $t('terminal.attach.header') }}</wa-dropdown-item>

        <template v-if="hasItems">
            <template v-for="section in sections" :key="section.scope">
                <template v-if="section.items.length">
                    <wa-divider></wa-divider>
                    <wa-dropdown-item disabled class="attach-terminal-section">{{ section.label }}</wa-dropdown-item>
                    <wa-dropdown-item
                        v-for="item in section.items"
                        :key="item.key"
                        :value="item.key"
                        :disabled="item.attached"
                    >
                        <wa-icon v-if="item.attached" slot="icon" name="check"></wa-icon>
                        {{ item.label }}
                    </wa-dropdown-item>
                </template>
            </template>
        </template>

        <template v-else>
            <wa-divider></wa-divider>
            <wa-dropdown-item disabled>{{ $t('terminal.attach.empty') }}</wa-dropdown-item>
        </template>
    </wa-dropdown>
</template>

<style scoped>
.attach-terminal-trigger {
    --wa-form-control-padding-inline: 0.3em;
}
.attach-terminal-trigger wa-icon + wa-icon {
    margin-inline-start: 0.15em;
    font-size: 0.7em;
}
.attach-terminal-header::part(label),
.attach-terminal-section::part(label) {
    font-weight: var(--wa-font-weight-semibold);
}
.attach-terminal-section::part(base) {
    opacity: 0.85;
}
</style>
