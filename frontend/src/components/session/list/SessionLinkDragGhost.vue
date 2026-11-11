<script setup>
// The sidebar session being dragged onto a chat (see composables/useSessionLinkDrag.js):
// a raised row that follows the pointer. Under a finger it sits above the touch
// point so the finger does not hide it.
import { computed } from 'vue'
import { sessionLinkDrag } from '../../../composables/useSessionLinkDrag.js'
import ProjectBadge from '../../project/ProjectBadge.vue'

const style = computed(() => ({ left: `${sessionLinkDrag.x}px`, top: `${sessionLinkDrag.y}px` }))
const touch = computed(() => sessionLinkDrag.pointerType !== 'mouse')
</script>

<template>
    <div
        v-if="sessionLinkDrag.session"
        class="session-link-drag-ghost"
        :class="{ 'session-link-drag-ghost--touch': touch }"
        :style="style"
        aria-hidden="true"
    >
        <span class="session-link-drag-ghost-title">{{ sessionLinkDrag.session.title }}</span>
        <ProjectBadge :project-id="sessionLinkDrag.session.projectId" class="session-link-drag-ghost-project" />
    </div>
</template>

<style scoped>
.session-link-drag-ghost {
    position: fixed;
    z-index: 10000;
    pointer-events: none;
    display: flex;
    flex-direction: column;
    gap: var(--wa-space-3xs);
    max-width: 18rem;
    padding: var(--wa-space-xs) var(--wa-space-s);
    border-radius: var(--wa-border-radius-m);
    background: var(--wa-color-surface-raised);
    box-shadow: var(--wa-shadow-l);
    opacity: 0.95;
    /* Mouse: beside the cursor. */
    transform: translate(12px, 12px);
}

/* Touch: centered above the finger. */
.session-link-drag-ghost--touch {
    transform: translate(-50%, calc(-100% - 24px));
}

.session-link-drag-ghost-title {
    font-weight: var(--wa-font-weight-semibold);
    white-space: nowrap;
    overflow: hidden;
    text-overflow: ellipsis;
}

.session-link-drag-ghost-project {
    font-size: var(--wa-font-size-xs);
}
</style>
