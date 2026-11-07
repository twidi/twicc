<script setup>
// Shared presentation for the compact row of agent-settings switches/toggles
// (context toggle, thinking, Chrome MCP, fast mode). Rows are assembled
// provider-agnostically by ``utils/agentSwitchRows.buildSwitchRows``; this
// component only renders them and emits the final typed value on change, so
// both the per-session popover and the per-provider defaults editor share the
// exact same widget. An optional notice icon + tooltip sits beside each switch.
import { useId } from 'vue'
import AppTooltip from '../ui/AppTooltip.vue'
import { AGENT_SETTING_ICONS } from '../../utils/agentSettingIcons'

const props = defineProps({
    // [{ field, kind: 'toggle'|'switch', label?, toggleLabel?, bigValue?,
    //    smallValue?, checked, disabled, notice? }]
    rows: { type: Array, default: () => [] },
})

const emit = defineEmits(['change'])

// Unique prefix for notice-icon ids (several instances may coexist in the DOM).
const uid = useId()

// A toggle writes its big/small value; a boolean switch writes the checkbox
// state. The host applies the emitted value (session ref or persisted default).
function onChange(row, event) {
    const checked = event.target.checked
    const value = row.kind === 'toggle' ? (checked ? row.bigValue : row.smallValue) : checked
    emit('change', { field: row.field, value })
}
</script>

<template>
    <!-- All switches (context toggle, thinking, Chrome MCP, fast mode) flow
         together in one wrapping row. -->
    <div v-if="rows.length" class="settings-switches">
        <!-- Boolean switch or the context toggle (ON = big value). An optional
             notice icon + tooltip (hover / click) sits beside the switch. -->
        <div v-for="row in rows" :key="row.field" class="setting-switch">
            <wa-switch
                size="small"
                :checked="row.checked"
                :disabled="row.disabled"
                @change="onChange(row, $event)"
            ><wa-icon
                v-if="AGENT_SETTING_ICONS[row.field]"
                class="setting-label-icon"
                :name="AGENT_SETTING_ICONS[row.field].icon || undefined"
                :src="AGENT_SETTING_ICONS[row.field].src || undefined"
                :family="AGENT_SETTING_ICONS[row.field].family || undefined"
                :style="{ color: AGENT_SETTING_ICONS[row.field].color }"
            ></wa-icon>{{ row.kind === 'toggle' ? row.toggleLabel : row.label }}</wa-switch>
            <template v-if="row.notice">
                <wa-icon
                    :id="`${uid}-notice-${row.field}`"
                    class="setting-notice-icon"
                    :class="`notice-${row.notice.variant}`"
                    :name="row.notice.icon"
                    tabindex="0"
                    role="button"
                    :label="row.notice.text"
                ></wa-icon>
                <AppTooltip
                    :for="`${uid}-notice-${row.field}`"
                    force
                    trigger="hover focus click"
                >{{ row.notice.text }}</AppTooltip>
            </template>
        </div>
    </div>
</template>

<style scoped>
.settings-switches {
    display: flex;
    flex-wrap: wrap;
    column-gap: var(--wa-space-m);
    row-gap: var(--wa-space-xs);
}

/* A switch (label inline in the wa-switch) + its optional notice icon, laid
   out on one line. */
.setting-switch {
    display: inline-flex;
    align-items: center;
    gap: var(--wa-space-3xs);
    font-size: var(--wa-font-size-s);
}

/* Setting glyph shown inside the switch label, right before the text. */
.setting-label-icon {
    margin-inline-end: 0.3em;
}

.setting-notice-icon {
    cursor: help;
    font-size: var(--wa-font-size-m);
}

.setting-notice-icon.notice-warning {
    color: var(--wa-color-warning-60);
}

.setting-notice-icon.notice-brand {
    color: var(--wa-color-brand-60);
}
</style>
