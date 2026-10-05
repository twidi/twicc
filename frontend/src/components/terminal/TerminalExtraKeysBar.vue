<script setup>
import { ref, computed, watch } from 'vue'
import { formatCombo } from '../../utils/terminalComboNotation'
import { useDataStore } from '../../stores/data'
import { useWorkspacesStore } from '../../stores/workspaces'
import AppTooltip from '../ui/AppTooltip.vue'
import ProjectMark from '../project/ProjectMark.vue'

const props = defineProps({
    activeModifiers: { type: Object, required: true },
    lockedModifiers: { type: Object, required: true },
    isTouchDevice: { type: Boolean, default: false },
    combos: { type: Array, default: () => [] },
    snippets: { type: Array, default: () => [] },
    terminals: { type: Array, default: () => [] },
    activeTerminalIndex: { type: Number, default: 0 },
})

const emit = defineEmits([
    'key-input', 'modifier-toggle', 'paste',
    'combo-press', 'snippet-press', 'snippet-disabled-press',
    'snippet-send-to', 'snippet-edit-send',
    'manage-combos', 'manage-snippets',
])

// ── Tab state ───────────────────────────────────────────────────────

// ── Key layout definition ───────────────────────────────────────────
// Each key has: label (display), key (emitted value), and optional flags
const BUILT_IN_TABS = {
    essentials: {
        keys: [
            { label: 'ESC', key: 'Escape' },
            { label: 'TAB', key: 'Tab' },
            { label: 'CTRL', key: 'ctrl', modifier: true },
            { label: 'ALT', key: 'alt', modifier: true },
            { label: '←', key: 'ArrowLeft', arrow: true },
            { label: '↑', key: 'ArrowUp', arrow: true },
            { label: '↓', key: 'ArrowDown', arrow: true },
            { label: '→', key: 'ArrowRight', arrow: true },
            { label: '-', key: '-' },
            { label: '/', key: '/' },
            { label: '|', key: '|' },
            { label: '~', key: '~' },
        ],
    },
    more: {
        keys: [
            { label: 'SHIFT', key: 'shift', modifier: true },
            { label: 'HOME', key: 'Home', wide: true },
            { label: 'END', key: 'End', wide: true },
            { label: 'PGUP', key: 'PageUp', wide: true },
            { label: 'PGDN', key: 'PageDown', wide: true },
            { label: 'DEL', key: 'Delete' },
            { label: 'INS', key: 'Insert' },
            { label: '\\', key: '\\' },
            { label: '_', key: '_' },
            { label: '*', key: '*' },
            { label: '&', key: '&' },
            { label: '.', key: '.' },
            { label: 'PASTE', key: 'paste', paste: true, wide: true },
        ],
    },
    fkeys: {
        keys: Array.from({ length: 12 }, (_, i) => ({
            label: `F${i + 1}`,
            key: `F${i + 1}`,
        })),
    },
}

const TAB_LABELS = {
    essentials: 'Essentials',
    more: 'More',
    fkeys: 'F-keys',
    custom: 'Custom',
    snippets: 'Snippets',
}

const tabIds = computed(() => {
    if (props.isTouchDevice) {
        return ['essentials', 'more', 'fkeys', 'custom', 'snippets']
    }
    return ['snippets']
})

const activeTab = ref(null)
watch(tabIds, (ids) => {
    if (!ids.includes(activeTab.value)) {
        activeTab.value = ids[0]
    }
}, { immediate: true })

// ── Double-tap detection for modifiers ──────────────────────────────
const lastModifierTap = {}

function handleModifierPointerDown(event, keyDef) {
    event.preventDefault() // Prevent focus theft from terminal

    const now = Date.now()
    const prev = lastModifierTap[keyDef.key] || 0
    const isDoubleTap = (now - prev) < 350
    lastModifierTap[keyDef.key] = now

    const mod = keyDef.key
    const isActive = props.activeModifiers[mod]
    const isLocked = props.lockedModifiers[mod]

    if (isActive && isDoubleTap && !isLocked) {
        // Was one-shot, double-tap → lock
        emit('modifier-toggle', mod, true)
    } else if (isActive) {
        // Was active (one-shot or locked) → deactivate
        emit('modifier-toggle', mod, false)
    } else if (isDoubleTap) {
        // Was inactive, double-tap → lock directly
        emit('modifier-toggle', mod, true)
    } else {
        // Was inactive, single tap → one-shot
        emit('modifier-toggle', mod, false)
    }
}

// ── Regular key handling ────────────────────────────────────────────
function handleKeyPointerDown(event, keyDef) {
    event.preventDefault() // Prevent focus theft from terminal
    emit('key-input', keyDef.key)
}

// ── Paste handling (uses @click, not @pointerdown) ──────────────────
function handlePasteClick() {
    emit('paste')
}

// ── Combo and snippet handling ──────────────────────────────────────
function handleComboPointerDown(event, combo) {
    event.preventDefault()
    emit('combo-press', combo)
}

function handleSnippetPointerDown(event, snippet) {
    event.preventDefault()
    if (snippet._disabled) {
        emit('snippet-disabled-press', snippet)
        return
    }
    emit('snippet-press', snippet)
}

function handleSnippetSendTo(event, snippet) {
    const value = event.detail?.item?.value
    if (!value) return
    if (value === 'edit') {
        emit('snippet-edit-send', snippet)
        return
    }
    emit('snippet-send-to', snippet, value)
}

// ── Snippet scope indicator ─────────────────────────────────────────
const dataStore = useDataStore()
const workspacesStore = useWorkspacesStore()

function snippetScopeInfo(snippet) {
    const scope = snippet._scope
    if (!scope) return null
    if (scope.startsWith('project:')) {
        const projectId = scope.slice(8)
        const project = dataStore.getProject(projectId)
        return { type: 'project', color: project?.color || null, projectId }
    }
    if (scope.startsWith('workspace:')) {
        const ws = workspacesStore.getWorkspaceById(scope.slice(10))
        return { type: 'workspace', color: ws?.color || null }
    }
    return null
}

// ── CSS classes for a key button ────────────────────────────────────
function keyClasses(keyDef) {
    return {
        'extra-key': true,
        'modifier': keyDef.modifier,
        'active-mod': keyDef.modifier && props.activeModifiers[keyDef.key],
        'locked': keyDef.modifier && props.lockedModifiers[keyDef.key],
        'arrow': keyDef.arrow,
        'wide': keyDef.wide,
    }
}
</script>

<template>
    <div class="extra-keys-bar">
        <!-- Tab bar: hidden when only 1 tab (desktop snippets-only) -->
        <div v-if="tabIds.length > 1" class="extra-keys-tabs">
            <button
                v-for="id in tabIds"
                :key="id"
                class="extra-keys-tab"
                :class="{ active: activeTab === id }"
                @pointerdown.prevent="activeTab = id"
            >
                {{ TAB_LABELS[id] }}
            </button>
        </div>

        <div class="extra-keys-keys">
            <!-- Built-in tabs (essentials, more, fkeys) -->
            <template v-if="BUILT_IN_TABS[activeTab]">
                <template v-for="keyDef in BUILT_IN_TABS[activeTab].keys" :key="keyDef.key">
                    <!-- Paste button -->
                    <button
                        v-if="keyDef.paste"
                        :class="keyClasses(keyDef)"
                        @click="handlePasteClick"
                    >
                        {{ keyDef.label }}
                    </button>
                    <!-- Modifier button -->
                    <button
                        v-else-if="keyDef.modifier"
                        :class="keyClasses(keyDef)"
                        @pointerdown="handleModifierPointerDown($event, keyDef)"
                    >
                        {{ keyDef.label }}
                    </button>
                    <!-- Regular key button -->
                    <button
                        v-else
                        :class="keyClasses(keyDef)"
                        @pointerdown="handleKeyPointerDown($event, keyDef)"
                    >
                        {{ keyDef.label }}
                    </button>
                </template>
            </template>

            <!-- Custom tab -->
            <template v-else-if="activeTab === 'custom'">
                <template v-if="combos.length > 0">
                    <button
                        v-for="(combo, i) in combos"
                        :key="'combo-' + i"
                        class="extra-key"
                        @pointerdown="handleComboPointerDown($event, combo)"
                    >
                        {{ formatCombo(combo) }}
                    </button>
                </template>
                <span v-else class="empty-tab-text">No custom combos</span>
                <button
                    class="extra-key manage-key"
                    @pointerdown.prevent="emit('manage-combos')"
                >
                    ⚙ Manage
                </button>
            </template>

            <!-- Snippets tab -->
            <template v-else-if="activeTab === 'snippets'">
                <template v-if="snippets.length > 0">
                    <template v-for="(snippet, i) in snippets" :key="'snippet-' + i">
                        <div class="snippet-group">
                            <button
                                :id="snippet._disabled ? `disabled-snippet-${i}` : undefined"
                                class="extra-key snippet-main"
                                :class="{ 'snippet-disabled': snippet._disabled }"
                                @pointerdown="handleSnippetPointerDown($event, snippet)"
                            >
                                <template v-if="snippetScopeInfo(snippet)?.type === 'project'">
                                    <ProjectMark
                                        class="snippet-scope-mark"
                                        :icon-url="dataStore.resolvedProjectIcons[snippetScopeInfo(snippet).projectId] || null"
                                        :color="snippetScopeInfo(snippet).color"
                                    />
                                </template>
                                <template v-else-if="snippetScopeInfo(snippet)?.type === 'workspace'">
                                    <wa-icon
                                        name="layer-group"
                                        class="snippet-scope-icon"
                                        :style="snippetScopeInfo(snippet).color ? { color: snippetScopeInfo(snippet).color } : null"
                                    ></wa-icon>
                                </template>
                                {{ snippet.label }}
                                <wa-icon v-if="snippet.openInNewTab" name="arrow-up-right-from-square" class="snippet-new-tab-icon"></wa-icon>
                            </button>
                            <wa-dropdown
                                placement="top-start"
                                @wa-select="(e) => handleSnippetSendTo(e, snippet)"
                            >
                                <button
                                    slot="trigger"
                                    class="extra-key snippet-arrow"
                                    :class="{ 'snippet-disabled': snippet._disabled }"
                                    @pointerdown.prevent
                                >
                                    <wa-icon name="chevron-up"></wa-icon>
                                </button>
                                <template v-if="!snippet._disabled">
                                    <wa-dropdown-item disabled class="dropdown-label">Send to tab</wa-dropdown-item>
                                    <wa-dropdown-item
                                        v-for="term in terminals"
                                        :key="term.index"
                                        :value="String(term.index)"
                                    >
                                        {{ term.label }}<template v-if="term.index === activeTerminalIndex"> (current)</template>
                                    </wa-dropdown-item>
                                    <wa-divider></wa-divider>
                                    <wa-dropdown-item value="new">
                                        <wa-icon slot="icon" name="plus"></wa-icon>
                                        New tab
                                    </wa-dropdown-item>
                                    <wa-divider></wa-divider>
                                </template>
                                <wa-dropdown-item value="edit">
                                    <wa-icon slot="icon" name="pen-to-square"></wa-icon>
                                    Edit before sending
                                </wa-dropdown-item>
                            </wa-dropdown>
                        </div>
                        <AppTooltip v-if="snippet._disabled" :for="`disabled-snippet-${i}`">
                            {{ snippet._disabledReason }}
                        </AppTooltip>
                    </template>
                </template>
                <span v-else class="empty-tab-text">No snippets</span>
                <button
                    class="extra-key manage-key"
                    @pointerdown.prevent="emit('manage-snippets')"
                >
                    ⚙ Manage
                </button>
            </template>
        </div>
    </div>
</template>

<style scoped>
/* ── Reset ── */
button {
    box-shadow: none !important;
    margin: 0;
}

/* ── Bar container ─────────────────────────────────────────────────── */
.extra-keys-bar {
    background: var(--wa-color-surface-lowered);
    border-top: 1px solid var(--wa-color-surface-border);
    padding: var(--wa-space-2xs) var(--wa-space-xs) var(--wa-space-xs);
    user-select: none;
    -webkit-user-select: none;
}

/* When the sidebar is closed, its reopen toggle overlaps the bottom-left. SessionLayout drives the
   exact amount per terminal position via --sidebar-toggle-clearance-extra-keys (it inherits across
   the panel teleport into this bar); the fallback covers terminals outside the dockable layout (e.g.
   the project view), where it resolves to the left-edge clearance + 1rem. */
:global(body.sidebar-toggle-floating .extra-keys-bar) {
    @media (width >= 640px) {
        padding-left: var(--sidebar-toggle-clearance-extra-keys, calc(var(--sidebar-toggle-clearance-left-x) + 1rem));
    }
}

:global(body.sidebar-toggle-floating .extra-keys-bar) {
    @media (width < 640px) {
        padding-left: 4.25rem;
    }
}

/* ── Tabs ───────────────────────────────────────────────────────────── */
.extra-keys-tabs {
    display: flex;
    gap: var(--wa-space-2xs);
    margin-bottom: var(--wa-space-2xs);
}

.extra-keys-tab {
    background: none;
    border: none;
    color: var(--wa-color-text-quiet);
    font-size: var(--wa-font-size-2xs);
    font-weight: var(--wa-font-weight-semibold);
    text-transform: uppercase;
    padding: var(--wa-space-3xs) var(--wa-space-xs);
    border-radius: var(--wa-border-radius-s);
    cursor: pointer;
    font-family: inherit;
    transition: color 0.15s, background-color 0.15s;
    touch-action: manipulation;
}

.extra-keys-tab:hover {
    color: var(--wa-color-text-normal);
    opacity: 0.7;
}

.extra-keys-tab.active {
    color: var(--wa-color-text-normal);
    background: var(--wa-color-surface-raised);
}

/* ── Key grid ──────────────────────────────────────────────────────── */
.extra-keys-keys {
    display: flex;
    gap: var(--wa-space-2xs);
    flex-wrap: wrap;
    align-items: center;
}

/* ── Key buttons ───────────────────────────────────────────────────── */
.extra-key {
    background: var(--wa-color-surface-raised);
    border: 1px solid var(--wa-color-surface-border);
    color: var(--wa-color-text-normal);
    font-family: var(--wa-font-family-code);
    font-size: var(--wa-font-size-xs);
    height: 1.75rem;
    min-width: 2rem;
    display: inline-flex;
    align-items: center;
    justify-content: center;
    gap: calc(var(--wa-form-control-padding-inline) / 2);
    border-radius: var(--wa-border-radius-s);
    cursor: pointer;
    transition: background-color 0.1s, border-color 0.1s, transform 0.1s;
    touch-action: manipulation;
    -webkit-user-select: none;
    user-select: none;
    line-height: 1;
}

.extra-key:has(wa-icon) {
    padding-right: calc(var(--wa-form-control-padding-inline) / 2);
}

.extra-key:hover {
    background: color-mix(in srgb, var(--wa-color-surface-raised), var(--wa-color-mix-hover));
}

.extra-key:active {
    background: color-mix(in srgb, var(--wa-color-surface-raised), var(--wa-color-mix-active));
    transform: scale(0.95);
}

/* Wide keys */
.extra-key.wide {
    min-width: 3rem;
}

/* Arrow keys */
.extra-key.arrow {
    font-size: var(--wa-font-size-s);
    min-width: 2rem;
    font-family: system-ui, sans-serif;
}

/* ── Modifier states ───────────────────────────────────────────────── */
.extra-key.modifier {
    color: var(--wa-color-brand-on-quiet);
    border-color: var(--wa-color-brand-border-quiet);
}

.extra-key.modifier:hover {
    background: var(--wa-color-brand-fill-quiet);
}

.extra-key.modifier.active-mod {
    background: var(--wa-color-brand-fill-normal);
    border-color: var(--wa-color-brand-border-normal);
    color: var(--wa-color-brand-on-normal);
    box-shadow: 0 0 var(--wa-space-xs) var(--wa-color-brand-fill-quiet);
}

.extra-key.modifier.locked {
    background: var(--wa-color-brand-fill-loud);
    border-color: var(--wa-color-brand-border-normal);
    color: var(--wa-color-brand-on-normal);
    box-shadow: 0 0 var(--wa-space-xs) var(--wa-color-brand-fill-quiet), inset 0 0 0 1px var(--wa-color-brand-border-normal);
}

/* ── Snippet split-button group ───────────────────────────────────── */
.snippet-group {
    display: inline-flex;
    align-items: stretch;
}

.snippet-group .snippet-main {
    border-top-right-radius: 0;
    border-bottom-right-radius: 0;
}

.snippet-group .snippet-arrow {
    border-top-left-radius: 0;
    border-bottom-left-radius: 0;
    border-left: none;
    min-width: unset;
    padding: 0 0.4rem;
    font-size: var(--wa-font-size-3xs);
    color: var(--wa-color-text-quiet);
    transition: background-color 0.1s, transform 0.1s;
}

.snippet-scope-mark {
    --project-mark-size: 0.85em;
    --project-mark-icon-size: var(--wa-space-m);
    flex-shrink: 0;
}

.snippet-scope-icon {
    font-size: 0.7em;
    flex-shrink: 0;
}

.snippet-new-tab-icon {
    font-size: 0.85em;
}

/* ── Disabled snippets ────────────────────────────────────────────── */
/* Main button: fully dimmed + not-allowed */
.extra-key.snippet-main.snippet-disabled {
    opacity: 0.35;
    cursor: not-allowed;
}

.extra-key.snippet-main.snippet-disabled:hover {
    background: var(--wa-color-surface-raised);
    transform: none;
}

.extra-key.snippet-main.snippet-disabled:active {
    transform: none;
}

/* Arrow button: dimmed but still interactive (opens edit-before-send) */
.extra-key.snippet-arrow.snippet-disabled {
    opacity: 0.35;
}

.extra-key.snippet-arrow.snippet-disabled:hover {
    opacity: 0.7;
}


/* ── Empty tab text ───────────────────────────────────────────────── */
.empty-tab-text {
    font-size: var(--wa-font-size-xs);
    opacity: 0.4;
    padding: var(--wa-space-2xs) var(--wa-space-xs);
    align-self: center;
}

/* ── Manage button ────────────────────────────────────────────────── */
.extra-key.manage-key {
    border-style: dashed;
    opacity: 0.6;
    font-size: var(--wa-font-size-xs);
}

.extra-key.manage-key:hover {
    opacity: 0.9;
}
</style>
