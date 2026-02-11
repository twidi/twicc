<script setup lang="ts">
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

withDefaults(defineProps<{
  nodeTheme?: NodeTheme
  breakPointTheme?: BreakPointTheme
  orientation?: GraphOrientation
  showCommitNodeHashes?: boolean
  showCommitNodeTooltips?: boolean
  highlightedBackgroundHeight?: number
}>(), {
  nodeTheme: 'default',
  breakPointTheme: 'dot',
  orientation: 'normal',
  showCommitNodeHashes: false,
  showCommitNodeTooltips: false,
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
