<script setup>
// ProjectMark.vue — a project's visual identity: its icon image when it has
// one, else the generated color dot (the historical rendering). Replaces the
// duplicated `--dot-color` circle markup across the app. Size is driven by the
// CSS var `--project-mark-size` (default `--wa-space-m`), overridable per call
// site. See docs/archives/plans/2026-07-17-project-icons-design.md.
import { computed, reactive } from 'vue'

const props = defineProps({
    // Resolved icon URL (server-side, inheritance included) or null → color dot.
    iconUrl: { type: String, default: null },
    // Dot color used when there is no icon (null → the neutral empty dot).
    color: { type: String, default: null },
})

// Icon URLs whose image failed to load — a project row keeps a manual icon in
// the database while the file is absent from the data dir (e.g. a worktree
// instance whose project-icons/ was not carried over). Without this the browser
// renders a broken-image box. Module-level and shared by every ProjectMark, so
// one failure degrades all occurrences to the color dot at once and no other
// instance re-requests the missing file. A replacement icon gets a new
// content-hashed URL, so entries never go stale in a harmful way.
const brokenIconUrls = reactive(new Set())

const showIcon = computed(() => !!props.iconUrl && !brokenIconUrls.has(props.iconUrl))

function onIconError() {
    if (props.iconUrl) brokenIconUrls.add(props.iconUrl)
}
</script>

<template>
    <span class="project-mark">
        <img
            v-if="showIcon"
            class="project-mark-icon"
            :src="iconUrl"
            alt=""
            loading="lazy"
            decoding="async"
            @error="onIconError"
        />
        <span
            v-else
            class="project-mark-dot"
            :style="color ? { '--dot-color': color } : null"
        ></span>
    </span>
</template>

<style scoped>
.project-mark {
    display: inline-flex;
    flex-shrink: 0;
    line-height: 0;
    /* Always use "m" size by default so the container for icons
    (m by default) and dots (s by default) use the same width) */
    width: var(--project-mark-icon-size, var(--project-mark-size, var(--wa-space-m)));
    justify-content: center;
}

.project-mark-dot {
    width: var(--project-mark-size, var(--wa-space-s));
    height: var(--project-mark-size, var(--wa-space-s));
    border-radius: 50%;
    box-sizing: border-box;
    border: 1px solid;
    background-color: var(--dot-color, transparent);
    border-color: var(--dot-color, var(--wa-color-border-quiet));
}

.project-mark-icon {
    width: var(--project-mark-icon-size, var(--project-mark-size, var(--wa-space-m)));
    height: var(--project-mark-icon-size, var(--project-mark-size, var(--wa-space-m)));
    max-width: unset; /* override any global `img { max-width: 100% }` reset */
    border-radius: 0; /* square — override any inherited/global rounding */
    object-fit: contain; /* whole image, centered, no crop and no distortion */
    display: block;
    box-sizing: border-box;
}
</style>
