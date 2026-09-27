<script setup>
/**
 * CommandPaletteButton — Clickable/tappable entry point that opens the
 * command palette.
 *
 * The palette is otherwise only reachable through the Cmd/Ctrl+K keyboard
 * shortcut, which leaves it inaccessible with the mouse and on touch devices.
 * This button mirrors that shortcut. A single CommandPalette instance is
 * mounted (in App.vue) and watches the registry's `isOpen`, so calling
 * `openPalette()` from anywhere is enough to open it.
 *
 * It carries a desktop-only tooltip showing the shortcut — AppTooltip hides
 * itself on touch devices, so nothing extra is needed for that.
 *
 * Designed to sit in the sidebar footer next to Inbox and Settings. Its local
 * query handles the historical three-action footer. ProjectView raises the
 * threshold when Inbox creates a four-action footer.
 */
import { useId, computed } from 'vue'
import { useCommandRegistry } from '../../composables/useCommandRegistry'
import { useSettingsStore } from '../../stores/settings'
import AppTooltip from '../ui/AppTooltip.vue'

const { openPalette } = useCommandRegistry()
const settingsStore = useSettingsStore()

const buttonId = useId()
// Match the modifier convention used by the Shortcuts panel (SettingsPopover).
const shortcut = computed(() => (settingsStore.isMac ? '⌘K' : 'Ctrl+K'))
</script>

<template>
    <wa-button
        :id="buttonId"
        class="command-palette-button"
        variant="brand"
        appearance="outlined"
        size="small"
        @click="openPalette"
    >
        <wa-icon name="bars-staggered"></wa-icon>
    </wa-button>
    <AppTooltip :for="buttonId">Open command palette ({{ shortcut }})</AppTooltip>
</template>

<style scoped>
/* Without Inbox, the sidebar footer degrades in three steps:
     1. toggle + palette + full Settings
     2. toggle + palette + Settings as a gear icon  (SettingsPopover, <= 15rem)
     3. toggle + Settings — the palette button drops out  (here, <= 9rem)
   So this hide threshold must stay BELOW SettingsPopover's compact threshold,
   leaving an intermediate band where the palette coexists with the compacted
   Settings; tune both together. Outside any `sidebar` container (e.g. a future
   placement on the home screen) this query never matches and the button stays
   visible. ProjectView supplies earlier reductions while Inbox is visible. */
@container sidebar (width <= 9rem) {
    .command-palette-button {
        display: none;
    }
}
</style>
