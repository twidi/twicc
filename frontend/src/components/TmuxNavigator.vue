<script setup>
import { ref, computed } from 'vue'
import { useSettingsStore } from '../stores/settings'

const settingsStore = useSettingsStore()

const props = defineProps({
    windows: {
        type: Array,
        default: () => [],
    },
    /** Array of { label, directory, presets: [...] } source groups. */
    presetSources: {
        type: Array,
        default: () => [],
    },
})

const emit = defineEmits(['select', 'create', 'edit-shortcut'])

const newName = ref('')

/** Set of running window names for quick lookup. */
const runningNames = computed(() => new Set(props.windows.map(w => w.name)))

/** All preset names across all sources, for ad-hoc filtering. */
const allPresetNames = computed(() => {
    const names = new Set()
    for (const source of props.presetSources) {
        for (const p of source.presets) {
            names.add(p.name)
        }
    }
    return names
})

/** Running windows that are NOT presets (ad-hoc shells). */
const adHocWindows = computed(() => {
    return props.windows.filter(w => !allPresetNames.value.has(w.name))
})

function handleSelect(name) {
    emit('select', name)
}

function handleCreate() {
    const name = newName.value.trim()
    if (!name) return
    emit('create', name)
    newName.value = ''
}

function handlePresetClick(preset) {
    if (runningNames.value.has(preset.name)) {
        emit('select', preset.name)
    } else {
        emit('create', preset)
    }
}
</script>

<template>
    <div class="tmux-navigator">
        <div class="navigator-content">
            <!-- Shortcut buttons config -->
            <h3 class="section-title">Shortcuts</h3>
            <div class="shortcut-slots">
                <button
                    v-for="(shortcut, index) in settingsStore.getTerminalShortcuts"
                    :key="index"
                    class="shortcut-slot"
                    :class="{ empty: !shortcut.label }"
                    @click="emit('edit-shortcut', index)"
                >
                    <template v-if="shortcut.label">
                        <span class="slot-label">{{ shortcut.label }}</span>
                        <wa-icon
                            v-if="shortcut.showOnDesktop"
                            name="display"
                            class="slot-desktop-icon"
                        ></wa-icon>
                    </template>
                    <template v-else>
                        <wa-icon name="circle-plus" class="slot-add-icon"></wa-icon>
                    </template>
                </button>
            </div>

            <!-- Shells section -->
            <h3 class="section-title shells-title">Shells</h3>

            <form class="create-form" @submit.prevent="handleCreate">
                <wa-input
                    :value="newName"
                    placeholder="Name"
                    size="small"
                    class="create-input"
                    @input="newName = $event.target.value"
                ></wa-input>
                <wa-button
                    type="submit"
                    variant="brand"
                    appearance="outlined"
                    size="small"
                    :disabled="!newName.trim()"
                >
                    Create
                </wa-button>
            </form>

            <!-- Ad-hoc windows (not from any preset) -->
            <div v-if="adHocWindows.length" class="shell-list">
                <wa-button
                    v-for="win in adHocWindows"
                    :key="win.name"
                    class="shell-button"
                    variant="neutral"
                    appearance="plain"
                    size="medium"
                    @click="handleSelect(win.name)"
                >
                    <span class="shell-row">
                        <wa-icon name="circle-play" class="shell-icon shell-icon--running"></wa-icon>
                        <span class="shell-label">{{ win.name }}</span>
                    </span>
                </wa-button>
            </div>

            <!-- Preset sources (one section per source, original order) -->
            <template v-for="source in presetSources" :key="source.directory">
                <h4 v-if="source.presets.length" class="presets-title">{{ source.label }}</h4>
                <div v-if="source.presets.length" class="shell-list">
                    <wa-button
                        v-for="preset in source.presets"
                        :key="preset.name"
                        class="shell-button"
                        variant="neutral"
                        appearance="plain"
                        size="medium"
                        @click="handlePresetClick(preset)"
                    >
                        <span class="shell-row">
                            <wa-icon name="circle-play" class="shell-icon" :class="{ 'shell-icon--running': runningNames.has(preset.name) }"></wa-icon>
                            <span class="shell-label">
                                <span>{{ preset.name }}</span>
                                <span v-if="preset.command" class="preset-command">{{ preset.command }}</span>
                            </span>
                        </span>
                    </wa-button>
                </div>
            </template>

        </div>
    </div>
</template>

<style scoped>
.tmux-navigator {
    height: 100%;
    display: flex;
    align-items: center;
    justify-content: center;
    background: var(--wa-color-surface-default);
    overflow-y: auto;
}

.navigator-content {
    display: flex;
    flex-direction: column;
    gap: var(--wa-space-m);
    width: min(400px, calc(100% - 2rem));
    padding: var(--wa-space-m) 0;
}

/* ── Shortcut slots ───────────────────────────────────────────────── */

.shortcut-slots {
    display: flex;
    gap: var(--wa-space-s);
    flex-wrap: wrap;
}

.shortcut-slot {
    display: flex;
    align-items: center;
    justify-content: center;
    gap: var(--wa-space-2xs);
    min-width: 5rem;
    padding: var(--wa-space-s) var(--wa-space-m);
    border: 2px solid var(--wa-color-border-default);
    border-radius: var(--wa-border-radius-m);
    background: var(--wa-color-surface-default);
    color: var(--wa-color-text-default);
    font-size: var(--wa-font-size-s);
    font-weight: var(--wa-font-weight-semibold);
    cursor: pointer;
    transition: border-color 0.15s, background 0.15s;
}

.shortcut-slot:hover {
    border-color: var(--wa-color-border-hover);
    background: var(--wa-color-surface-alt);
}

.shortcut-slot.empty {
    border-style: dashed;
    color: var(--wa-color-text-subtle);
    font-weight: normal;
}

.slot-label {
    white-space: nowrap;
}

.slot-desktop-icon {
    font-size: 0.85em;
    opacity: 0.5;
}

.slot-add-icon {
    font-size: 1.2em;
    opacity: 0.5;
}

/* ── Shells ────────────────────────────────────────────────────────── */

.section-title {
    margin: 0;
    font-size: var(--wa-font-size-l);
    font-weight: 600;
    color: var(--wa-color-text-default);
}

.shells-title {
    margin-top: var(--wa-space-l);
}

.presets-title {
    margin: 0;
    font-size: var(--wa-font-size-s);
    font-weight: 500;
    color: var(--wa-color-text-subtle);
    text-transform: uppercase;
    letter-spacing: 0.05em;
}

.shell-list {
    display: flex;
    flex-direction: column;
    gap: var(--wa-space-2xs);
}

.shell-button {
    width: 100%;
}

.shell-button::part(base) {
    justify-content: flex-start;
    width: 100%;
}

.shell-row {
    display: flex;
    flex-direction: row;
    align-items: flex-start;
    width: 100%;
}

.shell-icon {
    margin-right: var(--wa-space-xs);
    margin-top: 0.15em;
    font-size: 0.9em;
    flex-shrink: 0;
    opacity: 0.4;
}

.shell-icon--running {
    color: #4ade80;
    opacity: 1;
}

.shell-label {
    display: flex;
    flex-direction: column;
    align-items: flex-start;
    line-height: 1.3;
}

.preset-command {
    font-size: 0.8em;
    color: var(--wa-color-text-subtle);
    font-family: var(--wa-font-family-mono, monospace);
}

.create-form {
    display: flex;
    gap: var(--wa-space-xs);
    align-items: center;
    margin-top: var(--wa-space-s);
}

.create-input {
    flex: 1;
}
</style>
