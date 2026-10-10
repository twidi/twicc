<script setup lang="ts">
import type { PropType } from 'vue'
import type {
  BreakPointTheme,
  GraphOrientation,
  NodeTheme,
} from '../types'
import GraphCore from './GraphCore.vue'
import HTMLGridContent from './HTMLGridContent.vue'

// ---------------------------------------------------------------------------
// Props — forwarded to GraphCore
// ---------------------------------------------------------------------------

defineProps({
  nodeTheme: { type: String as PropType<NodeTheme>, required: false, default: 'default' },
  breakPointTheme: { type: String as PropType<BreakPointTheme>, required: false, default: 'dot' },
  orientation: { type: String as PropType<GraphOrientation>, required: false, default: 'normal' },
  showCommitNodeHashes: { type: Boolean, required: false, default: false },
  showCommitNodeTooltips: { type: Boolean, required: false, default: false },
  highlightedBackgroundHeight: { type: Number, required: false },
})
</script>

<template>
  <GraphCore
    :node-theme="nodeTheme"
    :break-point-theme="breakPointTheme"
    :orientation="orientation"
    :show-commit-node-hashes="showCommitNodeHashes"
    :show-commit-node-tooltips="showCommitNodeTooltips"
    :highlighted-background-height="highlightedBackgroundHeight"
  >
    <HTMLGridContent />
  </GraphCore>
</template>
