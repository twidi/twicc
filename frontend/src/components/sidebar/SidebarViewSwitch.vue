<script setup>
// SidebarViewSwitch.vue — a single square icon-button (sized like the sidebar's
// other header buttons) that toggles the sidebar between the Sessions list and
// the Artifacts (bookmarks) list. Lives in the sidebar header's context row,
// next to the scope selector — it's a *view* switch, not a list option, so it
// stays out of the options (sliders) menu.
//
// Its glyph overlays three icons inside the button: `comments` (sessions) in the
// top-left corner and `shapes` (artifacts) in the bottom-right, with a `slash`
// separator between them. The active view's icon is large and brand-coloured,
// the other small and muted; the slash flips direction with the mode. Only the
// scale and colour swap (the icons stay pinned to their corners) — transforms
// don't affect layout, so the button keeps a fixed size — and the swap animates.
import { computed, useId } from 'vue'
import AppTooltip from '../ui/AppTooltip.vue'
import { ARTIFACT_ICON } from '../../utils/artifactBookmark'

const props = defineProps({
    isArtifactsMode: { type: Boolean, default: false },
})

const emit = defineEmits(['toggle'])

const buttonId = `sidebar-view-switch-${useId()}`
const tooltip = computed(() =>
    props.isArtifactsMode ? 'Switch to sessions list' : 'Switch to artifacts list',
)
</script>

<template>
    <wa-button
        :id="buttonId"
        class="view-switch"
        :class="{ 'view-switch__icons--artifacts': isArtifactsMode }"
        size="small"
        appearance="outlined"
        variant="brand"
        @click="emit('toggle')"
    >
        <wa-icon name="slash" class="view-switch__icon view-switch__icon--slash"></wa-icon>
        <wa-icon name="comments" class="view-switch__icon view-switch__icon--sessions"></wa-icon>
        <wa-icon :name="ARTIFACT_ICON" class="view-switch__icon view-switch__icon--artifacts"></wa-icon>
    </wa-button>
    <AppTooltip :for="buttonId">{{ tooltip }}</AppTooltip>
</template>

<style scoped>
.view-switch {
    flex: 0;
    --icon-offset: 4px;
    --icon-max-scale: 0.9;
    --icon-min-scale: 0.5;
    --icon-color-active: var(--wa-color-brand);
    --icon-color-inactive: var(--wa-color-text-quiet);
    &::part(base) {
        padding:0;
        position: relative;
    }
}

.view-switch__icon {
    transition: transform 0.5s ease, color 0.5s ease;
    &:not(.view-switch__icon--slash) {
        position: absolute;
    }
}

/* Slash separator */
.view-switch__icon--slash {
    --icon-slash-offset: 1;
    transform: scaleX(-1)
               translateX(calc(var(--icon-slash-offset) * -1 * var(--icon-offset)))
               translateY(calc(var(--icon-slash-offset) * var(--icon-offset)))
               ;
    opacity: 0.5;
}

/* Sessions (chat): pinned top-left, scales from that corner. */
.view-switch__icon--sessions {
    left: var(--icon-offset);
    top: calc(1px + var(--icon-offset));
    transform-origin: top left;
    transform: scale(var(--icon-max-scale));
    color: var(--icon-color-active);
}

/* Artifacts (shapes): pinned bottom-right, scales from that corner. */
.view-switch__icon--artifacts {
    --icon-max-scale: 1.1;
    right: var(--icon-offset);
    bottom: var(--icon-offset);
    transform-origin: bottom right;
    transform: scale(var(--icon-min-scale));
    color: var(--icon-color-inactive);
}

/* Artifacts mode: invert the emphasis — scale + colour swap, and the slash flips. */
.view-switch__icons--artifacts {
    .view-switch__icon--sessions {
        transform: scale(var(--icon-min-scale));
        color: var(--icon-color-inactive);
    }
    .view-switch__icon--artifacts {
        transform: scale(var(--icon-max-scale));
        color: var(--icon-color-active);
    }
    .view-switch__icon--slash {
        --icon-slash-offset: -1;
    }
}
</style>
