// ---------------------------------------------------------------------------
// Components
// ---------------------------------------------------------------------------

export { default as GitLog } from './GitLog.vue'
export { default as GitLogGraphHTMLGrid } from './graph/HTMLGridGraph.vue'
export { default as GitLogTable } from './table/GitLogTable.vue'
export { default as GitLogTags } from './tags/GitLogTags.vue'

// ---------------------------------------------------------------------------
// Composables
// ---------------------------------------------------------------------------

export { useGitContext } from './composables/useGitContext'
export { useThemeContext } from './composables/useThemeContext'

// ---------------------------------------------------------------------------
// Types
//
// Public types for consumers of the GitLog component.
//
// Scoped slots available on the components:
//
//   GitLog:
//     #tags   — Slot for the GitLogTags component
//     #graph  — Slot for the GitLogGraphHTMLGrid component
//     #table  — Slot for the GitLogTable component
//
//   GitLogTable:
//     #row="{ commit, selected, previewed, backgroundColour }"
//       Custom table row (see CustomTableRowProps for slot prop types)
//
// ---------------------------------------------------------------------------

export type {
  // Entry data (consumer-provided)
  GitLogEntry,
  GitLogEntryBase,

  // Enriched commit (internal representation exposed via callbacks/slots)
  Commit,
  CommitBase,
  CommitAuthor,

  // Main component props
  GitLogProps,
  GitLogCommonProps,

  // Graph props
  HTMLGridGraphProps,
  GraphPropsCommon,
  GraphOrientation,

  // Table props
  TableProps,
  GitLogTableStylingProps,

  // Scoped slot prop types
  CustomCommitNodeProps,
  CustomTooltipProps,
  CustomTableRowProps,

  // Filtering
  CommitFilter,

  // Pagination
  GitLogPaging,
  GraphPaging,

  // Git index
  GitLogIndexStatus,

  // Theme
  ThemeMode,
  ThemeColours,
  NodeTheme,
  BreakPointTheme,
  ThemeFunctions,
  GetCommitNodeColoursArgs,
  CommitNodeColours,

  // URLs
  GitLogUrls,
  GitLogUrlBuilder,
  GitLogUrlBuilderArgs,

  // Styling
  GitLogStylingProps,
} from './types'
