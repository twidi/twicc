<script setup lang="ts">
import { useId, inject, type CSSProperties, type PropType } from 'vue'
import IndexStatus from './IndexStatus.vue'
import AppTooltip from '../../../../ui/AppTooltip.vue'


// ---------------------------------------------------------------------------
// Props
// ---------------------------------------------------------------------------

const props = defineProps({
  index: { type: Number, required: true },
  isIndex: { type: Boolean, required: true },
  commitHash: { type: String, required: true },
  commitMessage: { type: String, required: true },
  style: { type: Object as PropType<CSSProperties>, required: false },
})

const messageId = useId()

// Optional inject: a function (hash: string) => boolean that tells whether
// a commit has decorations (e.g. code comments). Provided by the host app,
// not by the GitLog library itself. When not provided, no decoration is shown.
const commitHasDecoration = inject<((hash: string) => boolean) | null>('gitCommitHasDecoration', null)
</script>

<template>
  <div
    :id="messageId"
    :class="['message', {isIndex, hasDecoration: commitHasDecoration?.(commitHash)}]"
    :style="style"
  >
    <span class="message-text">{{ commitMessage }}</span>
    <IndexStatus v-if="isIndex" />
    <wa-icon
      v-if="commitHasDecoration?.(commitHash)"
      name="comment"
      variant="regular"
      class="commit-decoration-icon"
    ></wa-icon>
  </div>
  <AppTooltip :for="messageId">{{ commitMessage }}</AppTooltip>
</template>

<style scoped>
.message {
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
  padding-inline: 1rem;
  font-weight: 400;
  &.isIndex, &.hasDecoration {
    display: flex;
    align-items: center;
  }
}
.message-text {
  overflow: hidden;
  text-overflow: ellipsis;
}
.commit-decoration-icon {
  flex-shrink: 0;
  color: var(--wa-color-brand, #6366f1);
  font-size: 0.75em;
  margin-left: 0.4em;
}
</style>
