<script setup>
import { ref } from 'vue'

const props = defineProps({
    activeModifiers: {
        type: Object,
        required: true,
    },
    lockedModifiers: {
        type: Object,
        required: true,
    },
    theme: {
        type: String,
        default: 'dark',
    },
})

const emit = defineEmits(['key-input', 'modifier-toggle', 'paste'])

// ── Tab state ───────────────────────────────────────────────────────
const activeTab = ref('essentials')

// ── Key layout definition ───────────────────────────────────────────
// Each key has: label (display), key (emitted value), and optional flags
const TABS = {
    essentials: {
        label: 'Essentials',
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
        label: 'More',
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
        label: 'F-keys',
        keys: Array.from({ length: 12 }, (_, i) => ({
            label: `F${i + 1}`,
            key: `F${i + 1}`,
        })),
    },
}

const tabIds = Object.keys(TABS)

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
    <div class="extra-keys-bar" :class="theme">
        <div class="extra-keys-tabs">
            <button
                v-for="id in tabIds"
                :key="id"
                class="extra-keys-tab"
                :class="{ active: activeTab === id }"
                @pointerdown.prevent="activeTab = id"
            >
                {{ TABS[id].label }}
            </button>
        </div>
        <div class="extra-keys-keys">
            <template v-for="keyDef in TABS[activeTab].keys" :key="keyDef.key">
                <!-- Paste button: uses @click only (no @pointerdown.prevent)
                     to preserve the full user activation chain required
                     by navigator.clipboard.readText(). Focus is restored
                     by the handler via terminal.focus() after paste. -->
                <button
                    v-if="keyDef.paste"
                    :class="keyClasses(keyDef)"
                    @click="handlePasteClick"
                >
                    {{ keyDef.label }}
                </button>
                <!-- Modifier button: has special double-tap logic -->
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
        </div>
    </div>
</template>

<style scoped>
/* ── Bar container ─────────────────────────────────────────────────── */
.extra-keys-bar {
    background: #141e28;
    border-top: 1px solid #2a3a4a;
    padding: 4px 6px 6px;
    user-select: none;
    -webkit-user-select: none;
}

/* When sidebar is closed, the sidebar toggle button overlaps
   the left edge. Add left padding to make room. */
:global(body.sidebar-closed .extra-keys-bar) {
    @media (width >= 640px) {
        padding-left: 4rem;
    }
}

.extra-keys-bar {
    @media (width < 640px) {
        padding-left: 4.25rem;
    }
}

/* ── Tabs ───────────────────────────────────────────────────────────── */
.extra-keys-tabs {
    display: flex;
    gap: 2px;
    margin-bottom: 5px;
}

.extra-keys-tab {
    background: none;
    border: none;
    color: #5a6a7a;
    font-size: 10px;
    font-weight: 600;
    text-transform: uppercase;
    letter-spacing: 0.5px;
    padding: 3px 8px;
    border-radius: 3px;
    cursor: pointer;
    font-family: inherit;
    transition: color 0.15s, background-color 0.15s;
    touch-action: manipulation;
}

.extra-keys-tab:hover {
    color: #8a9aaa;
}

.extra-keys-tab.active {
    color: #c0d0e0;
    background: rgba(255, 255, 255, 0.08);
}

/* ── Key grid ──────────────────────────────────────────────────────── */
.extra-keys-keys {
    display: flex;
    gap: 4px;
    flex-wrap: wrap;
}

/* ── Key buttons ───────────────────────────────────────────────────── */
.extra-key {
    background: #1e2d3d;
    border: 1px solid #2a3a4a;
    color: #c0d0e0;
    font-family: 'Fira Code', 'Cascadia Code', 'JetBrains Mono', monospace;
    font-size: 12px;
    height: 28px;
    min-width: 34px;
    display: inline-flex;
    align-items: center;
    justify-content: center;
    border-radius: 4px;
    cursor: pointer;
    transition: background-color 0.1s, border-color 0.1s, transform 0.1s;
    touch-action: manipulation;
    -webkit-user-select: none;
    user-select: none;
    line-height: 1;
}

.extra-key:hover {
    background: #263a4d;
    border-color: #3a4a5a;
}

.extra-key:active {
    background: #2a4050;
    transform: scale(0.95);
}

/* Wide keys */
.extra-key.wide {
    min-width: 42px;
}

/* Arrow keys */
.extra-key.arrow {
    font-size: 14px;
    min-width: 30px;
    font-family: system-ui, sans-serif;
}

/* ── Modifier states ───────────────────────────────────────────────── */
.extra-key.modifier {
    color: #c084fc;
    border-color: #3a2d5a;
}

.extra-key.modifier:hover {
    background: #2a2545;
}

.extra-key.modifier.active-mod {
    background: #3a2560;
    border-color: #7c5cbf;
    color: #d8b4fe;
    box-shadow: 0 0 6px rgba(192, 132, 252, 0.3);
}

.extra-key.modifier.locked {
    background: #3a2560;
    border-color: #9b7ad8;
    color: #e0c4ff;
    box-shadow: 0 0 6px rgba(192, 132, 252, 0.3), inset 0 0 0 1px rgba(192, 132, 252, 0.3);
}

/* ── Light theme ───────────────────────────────────────────────────── */
.extra-keys-bar.light {
    background: #f0f2f4;
    border-top-color: #d0d7de;
}

.light .extra-keys-tab {
    color: #8b949e;
}

.light .extra-keys-tab:hover {
    color: #57606a;
}

.light .extra-keys-tab.active {
    color: #24292e;
    background: rgba(0, 0, 0, 0.06);
}

.light .extra-key {
    background: #ffffff;
    border-color: #d0d7de;
    color: #24292e;
}

.light .extra-key:hover {
    background: #f6f8fa;
    border-color: #b0b8c0;
}

.light .extra-key:active {
    background: #eaeef2;
}

.light .extra-key.modifier {
    color: #8250df;
    border-color: #c8b8e8;
}

.light .extra-key.modifier:hover {
    background: #f5f0ff;
}

.light .extra-key.modifier.active-mod {
    background: #ede4ff;
    border-color: #8250df;
    color: #6e3dc7;
    box-shadow: 0 0 4px rgba(130, 80, 223, 0.2);
}

.light .extra-key.modifier.locked {
    background: #ede4ff;
    border-color: #6e3dc7;
    color: #5b21b6;
    box-shadow: 0 0 4px rgba(130, 80, 223, 0.2), inset 0 0 0 1px rgba(130, 80, 223, 0.2);
}
</style>
