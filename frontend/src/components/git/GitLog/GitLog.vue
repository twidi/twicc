<script setup lang="ts">
import { computed, provide, ref, useSlots, type PropType } from 'vue'
import { useI18n } from 'vue-i18n'
import dayjs from 'dayjs'
import utc from 'dayjs/plugin/utc'

import type {
  Commit,
  GitLogEntry,
  CommitFilter,
  GitLogIndexStatus,
  GitLogStylingProps,
  GitLogUrlBuilder,
  GraphOrientation,
  GraphPaging,
  ThemeColours,
  ColorScheme,
} from './types'
import { DEFAULT_GRAPH_COLUMN_WIDTH, DEFAULT_NODE_SIZE, DEFAULT_HEADER_ROW_HEIGHT, DEFAULT_ROW_HEIGHT, DEFAULT_SCROLL_BUFFER } from './constants'
import {
  computeRelationships,
  GraphDataBuilder,
  temporalTopologicalSort,
} from './data'
import type { GraphData } from './data/types'
import {
  GIT_CONTEXT_KEY,
  THEME_CONTEXT_KEY,
  type GitContextBag,
  type ThemeContextBag,
} from './composables/keys'
import { generateRainbowGradient } from './utils/createRainbowTheme'
import { neonAuroraDarkColours, neonAuroraLightColours } from './utils/colors'
import Layout from './Layout.vue'

dayjs.extend(utc)

const { t } = useI18n()

// ---------------------------------------------------------------------------
// Props
// ---------------------------------------------------------------------------

const props = defineProps({
  entries: { type: Array as PropType<GitLogEntry[]>, required: true },
  currentBranch: { type: String, required: true },
  colorScheme: { type: String as PropType<ColorScheme>, required: false, default: 'light' },
  colours: { type: [String, Array] as PropType<ThemeColours | string[]>, required: false, default: 'rainbow-light' },
  filter: { type: Function as PropType<CommitFilter>, required: false },
  showGitIndex: { type: Boolean, required: false, default: true },
  indexStatus: { type: Object as PropType<GitLogIndexStatus>, required: false },
  headCommitHash: { type: String, required: false },
  urls: { type: Function as PropType<GitLogUrlBuilder>, required: false },
  onSelectCommit: { type: Function as PropType<(commit?: Commit) => void>, required: false },
  onPreviewCommit: { type: Function as PropType<(commit?: Commit) => void>, required: false },
  showHeaders: { type: Boolean, required: false, default: false },
  rowHeight: { type: Number, required: false, default: DEFAULT_ROW_HEIGHT },
  headerRowHeight: { type: Number, required: false, default: DEFAULT_HEADER_ROW_HEIGHT },
  nodeSize: { type: Number, required: false, default: DEFAULT_NODE_SIZE },
  graphColumnWidth: { type: Number, required: false, default: DEFAULT_GRAPH_COLUMN_WIDTH },
  scrollBuffer: { type: Number, required: false, default: DEFAULT_SCROLL_BUFFER },
  enableSelectedCommitStyling: { type: Boolean, required: false, default: true },
  enablePreviewedCommitStyling: { type: Boolean, required: false, default: true },
  classes: { type: Object as PropType<GitLogStylingProps>, required: false },
})

// ---------------------------------------------------------------------------
// Slots detection (for showBranchesTags / showTable)
// ---------------------------------------------------------------------------

const slots = useSlots()

// ---------------------------------------------------------------------------
// Internal state
// ---------------------------------------------------------------------------

const selectedCommit = ref<Commit>()
const previewedCommit = ref<Commit>()
const graphOrientation = ref<GraphOrientation>('normal')

// ---------------------------------------------------------------------------
// Data pipeline (computed)
// ---------------------------------------------------------------------------

const pipelineResult = computed(() => {
  const { children, parents, hashToCommit } = computeRelationships(
    props.entries,
    props.headCommitHash,
  )

  const sortedCommits = temporalTopologicalSort(
    [...hashToCommit.values()],
    children,
    hashToCommit,
  )

  const filteredCommits = props.filter?.(sortedCommits) ?? sortedCommits

  const graphDataBuilder = new GraphDataBuilder({
    commits: sortedCommits,
    filteredCommits,
    children,
    parents,
    currentBranch: props.currentBranch,
  })

  const { graphColumns, positions, edges } = graphDataBuilder.build()

  const graphData: GraphData = {
    children,
    parents,
    hashToCommit,
    graphColumns,
    positions,
    edges,
    commits: filteredCommits,
  }

  return { graphData, allCommits: sortedCommits }
})

const graphData = computed(() => pipelineResult.value.graphData)
const allCommits = computed(() => pipelineResult.value.allCommits)

// ---------------------------------------------------------------------------
// Head commit & index pseudo-commit
// ---------------------------------------------------------------------------

const headCommit = computed<Commit | undefined>(() => {
  return allCommits.value.find(({ branch }) => {
    return branch.includes(props.currentBranch)
  })
})

const indexCommit = computed<Commit | undefined>(() => {
  if (!headCommit.value) {
    return undefined
  }

  const today = dayjs.utc().toISOString()

  return {
    hash: 'index',
    branch: headCommit.value.branch,
    parents: [headCommit.value.hash],
    children: [],
    authorDate: today,
    message: t('git.index.uncommittedChanges'),
    committerDate: today,
    isBranchTip: false,
  } as Commit
})

// ---------------------------------------------------------------------------
// Scroll-driven paging (set by Layout via setPaging)
// ---------------------------------------------------------------------------

const scrollPaging = ref<GraphPaging>()

function setPaging(paging: GraphPaging): void {
  scrollPaging.value = paging
}

// ---------------------------------------------------------------------------
// Index visibility
// ---------------------------------------------------------------------------

const isIndexVisible = computed<boolean>(() => {
  if (!props.showGitIndex) {
    return false
  }

  const p = scrollPaging.value
  if (p) {
    return p.startIndex === 0
  }

  return true
})

// ---------------------------------------------------------------------------
// Graph container width
// ---------------------------------------------------------------------------


// ---------------------------------------------------------------------------
// Selection / preview handlers
// ---------------------------------------------------------------------------

function handleSelectCommit(commit?: Commit): void {
  selectedCommit.value = commit
  props.onSelectCommit?.(commit)
}

function handlePreviewCommit(commit?: Commit): void {
  previewedCommit.value = commit
  props.onPreviewCommit?.(commit)
}

// ---------------------------------------------------------------------------
// Theme colour resolution
// (equivalent to ThemeContextProvider in the React source)
// ---------------------------------------------------------------------------

/**
 * Blends an RGB colour with a background colour to simulate alpha.
 * Standalone version used during colour bootstrap (before ThemeContext exists).
 */
function bootstrapShiftAlphaChannel(rgb: string, opacity: number, colorScheme: ColorScheme): string {
  const matches = rgb?.match(/\d+/g)

  if (rgb && matches != null) {
    const [rFg, gFg, bFg] = matches.map(Number)
    const backgroundRgb = colorScheme === 'dark' ? 'rgb(0, 0, 0)' : 'rgb(255, 255, 255)'
    const [rBg, gBg, bBg] = backgroundRgb.match(/\d+/g)!.map(Number)

    const rNew = Math.round(rFg * opacity + rBg * (1 - opacity))
    const gNew = Math.round(gFg * opacity + gBg * (1 - opacity))
    const bNew = Math.round(bFg * opacity + bBg * (1 - opacity))

    return `rgb(${rNew}, ${gNew}, ${bNew})`
  }

  return rgb
}

const resolvedColours = computed<string[]>(() => {
  const coloursProp = props.colours
  const width = graphData.value.graphColumns

  if (typeof coloursProp === 'string') {
    switch (coloursProp) {
      case 'rainbow-light':
        return generateRainbowGradient(width + 1)
      case 'rainbow-dark':
        return generateRainbowGradient(width + 1)
          .map(colour => bootstrapShiftAlphaChannel(colour, 0.6, props.colorScheme))
      case 'neon-aurora-dark':
        return neonAuroraDarkColours
      case 'neon-aurora-light':
        return neonAuroraLightColours
    }
  }

  // Custom array of colours
  if (props.colorScheme === 'light') {
    return coloursProp
  }

  return coloursProp.map(colour => bootstrapShiftAlphaChannel(colour, 0.6, props.colorScheme))
})

// ---------------------------------------------------------------------------
// Provide: ThemeContext
// ---------------------------------------------------------------------------

const themeContextValue: ThemeContextBag = {
  colorScheme: computed(() => props.colorScheme),
  colours: resolvedColours,
}

provide(THEME_CONTEXT_KEY, themeContextValue)

// ---------------------------------------------------------------------------
// Provide: GitContext
// ---------------------------------------------------------------------------

const gitContextValue: GitContextBag = {
  currentBranch: computed(() => props.currentBranch),
  headCommit,
  headCommitHash: computed(() => props.headCommitHash),
  indexCommit,
  selectedCommit: computed(() => selectedCommit.value),
  setSelectedCommit: handleSelectCommit,
  previewedCommit: computed(() => previewedCommit.value),
  setPreviewedCommit: handlePreviewCommit,
  enableSelectedCommitStyling: computed(() => props.enableSelectedCommitStyling),
  enablePreviewedCommitStyling: computed(() => props.enablePreviewedCommitStyling),
  showBranchesTags: computed(() => !!slots.tags),
  showTable: computed(() => !!slots.table),
  showHeaders: computed(() => props.showHeaders),
  remoteProviderUrlBuilder: computed(() => props.urls),
  rowHeight: computed(() => props.rowHeight),
  headerRowHeight: computed(() => props.headerRowHeight),
  graphColumnWidth: computed(() => props.graphColumnWidth),
  graphData,
  classes: computed(() => props.classes),
  indexStatus: computed(() => props.indexStatus),
  isServerSidePaginated: computed(() => false),
  paging: computed(() => scrollPaging.value),
  setPaging,
  isIndexVisible,
  nodeSize: computed(() => props.nodeSize),
  scrollBuffer: computed(() => props.scrollBuffer),
  graphOrientation: computed(() => graphOrientation.value),
  setGraphOrientation: (orientation: GraphOrientation) => { graphOrientation.value = orientation },
}

provide(GIT_CONTEXT_KEY, gitContextValue)

</script>

<template>
  <Layout>
    <template v-if="$slots.tags" #tags>
      <slot name="tags" />
    </template>

    <template v-if="$slots.graph" #graph>
      <slot name="graph" />
    </template>

    <template v-if="$slots.table" #table>
      <slot name="table" />
    </template>
  </Layout>
</template>
