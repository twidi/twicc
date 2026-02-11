import type { InjectionKey, Ref } from 'vue'
import type {
  Commit,
  CommitFilter,
  CustomCommitNodeProps,
  CustomTooltipProps,
  GitLogIndexStatus,
  GitLogStylingProps,
  GitLogUrlBuilder,
  GraphOrientation,
  GraphPaging,
  BreakPointTheme,
  NodeTheme,
  ThemeMode,
} from '../types'
import type { GraphData } from '../data/types'
import type { GraphMatrixColumns } from '../graph/GraphMatrixBuilder/GraphMatrixColumns'

// ---------------------------------------------------------------------------
// RowIndexToColumnStates — map used by the graph matrix
// ---------------------------------------------------------------------------

export type RowIndexToColumnStates = Map<number, GraphMatrixColumns>

// ---------------------------------------------------------------------------
// GitContextBag
// ---------------------------------------------------------------------------

export interface GitContextBag {
  currentBranch: Readonly<Ref<string>>
  headCommit: Readonly<Ref<Commit | undefined>>
  headCommitHash: Readonly<Ref<string | undefined>>
  indexCommit: Readonly<Ref<Commit | undefined>>
  selectedCommit: Readonly<Ref<Commit | undefined>>
  setSelectedCommit: (commit?: Commit) => void
  previewedCommit: Readonly<Ref<Commit | undefined>>
  setPreviewedCommit: (commit?: Commit) => void
  enableSelectedCommitStyling: Readonly<Ref<boolean>>
  enablePreviewedCommitStyling: Readonly<Ref<boolean>>
  showBranchesTags: Readonly<Ref<boolean>>
  showTable: Readonly<Ref<boolean>>
  showHeaders: Readonly<Ref<boolean>>
  remoteProviderUrlBuilder: Readonly<Ref<GitLogUrlBuilder | undefined>>
  rowHeight: Readonly<Ref<number>>
  headerRowHeight: Readonly<Ref<number>>
  graphWidth: Readonly<Ref<number>>
  setGraphWidth: (width: number) => void
  graphData: Readonly<Ref<GraphData>>
  classes: Readonly<Ref<GitLogStylingProps | undefined>>
  indexStatus: Readonly<Ref<GitLogIndexStatus | undefined>>
  isServerSidePaginated: Readonly<Ref<boolean>>
  paging: Readonly<Ref<GraphPaging | undefined>>
  isIndexVisible: Readonly<Ref<boolean>>
  filter: Readonly<Ref<CommitFilter | undefined>>
  nodeSize: Readonly<Ref<number>>
  graphOrientation: Readonly<Ref<GraphOrientation>>
  setGraphOrientation: (orientation: GraphOrientation) => void
}

// ---------------------------------------------------------------------------
// ThemeContextBag
// ---------------------------------------------------------------------------

export interface ThemeContextBag {
  theme: Readonly<Ref<ThemeMode>>
  colours: Readonly<Ref<string[]>>
}

// ---------------------------------------------------------------------------
// GraphContextBag
// ---------------------------------------------------------------------------

export interface GraphContextBag {
  showCommitNodeHashes: Readonly<Ref<boolean>>
  showCommitNodeTooltips: Readonly<Ref<boolean>>
  node: Readonly<Ref<((props: CustomCommitNodeProps) => unknown) | undefined>>
  highlightedBackgroundHeight: Readonly<Ref<number | undefined>>
  nodeTheme: Readonly<Ref<NodeTheme>>
  breakPointTheme: Readonly<Ref<BreakPointTheme>>
  nodeSize: Readonly<Ref<number>>
  graphWidth: Readonly<Ref<number>>
  orientation: Readonly<Ref<GraphOrientation>>
  visibleCommits: Readonly<Ref<Commit[]>>
  isHeadCommitVisible: Readonly<Ref<boolean>>
  columnData: Readonly<Ref<RowIndexToColumnStates>>
  tooltip: Readonly<Ref<((props: CustomTooltipProps) => unknown) | undefined>>
}

// ---------------------------------------------------------------------------
// TableContextBag
// ---------------------------------------------------------------------------

export interface TableContextBag {
  timestampFormat: Readonly<Ref<string | undefined>>
}

// ---------------------------------------------------------------------------
// Injection keys
// ---------------------------------------------------------------------------

export const GIT_CONTEXT_KEY: InjectionKey<GitContextBag> = Symbol('GitContext')
export const THEME_CONTEXT_KEY: InjectionKey<ThemeContextBag> = Symbol('ThemeContext')
export const GRAPH_CONTEXT_KEY: InjectionKey<GraphContextBag> = Symbol('GraphContext')
export const TABLE_CONTEXT_KEY: InjectionKey<TableContextBag> = Symbol('TableContext')
