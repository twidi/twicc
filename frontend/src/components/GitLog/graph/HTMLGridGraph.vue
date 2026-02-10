<script setup lang="ts">
import { DEFAULT_NODE_SIZE } from '../constants'
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
  nodeSize?: number
  nodeTheme?: NodeTheme
  breakPointTheme?: BreakPointTheme
  orientation?: GraphOrientation
  enableResize?: boolean
  showCommitNodeHashes?: boolean
  showCommitNodeTooltips?: boolean
  highlightedBackgroundHeight?: number
}>(), {
  nodeSize: DEFAULT_NODE_SIZE,
  nodeTheme: 'default',
  breakPointTheme: 'dot',
  orientation: 'normal',
  enableResize: false,
  showCommitNodeHashes: false,
  showCommitNodeTooltips: false,
})
</script>

<template>
  <GraphCore
    :node-size="nodeSize"
    :node-theme="nodeTheme"
    :break-point-theme="breakPointTheme"
    :orientation="orientation"
    :enable-resize="enableResize"
    :show-commit-node-hashes="showCommitNodeHashes"
    :show-commit-node-tooltips="showCommitNodeTooltips"
    :highlighted-background-height="highlightedBackgroundHeight"
  >
    <HTMLGridContent />
  </GraphCore>
</template>
