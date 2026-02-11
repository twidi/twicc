import type { ComputedRef, CSSProperties } from 'vue'

// ---------------------------------------------------------------------------
// GitLogEntry — input data provided by the consumer
// ---------------------------------------------------------------------------

export interface GitLogEntryBase {
  /**
   * The unique hash identifier of the commit.
   */
  hash: string

  /**
   * The name of the branch this commit belongs to.
   */
  branch: string

  /**
   * An array of parent commit hashes.
   *
   * - If this commit is a merge commit, it will have multiple parents.
   * - If this commit is an initial commit, it will have no parents.
   */
  parents: string[]

  /**
   * The commit message describing the changes made in this commit.
   */
  message: string

  /**
   * Details of the user who authored
   * the commit.
   */
  author?: CommitAuthor

  /**
   * The date and time when the commit was applied by the committer.
   *
   * This is typically the timestamp when the commit was finalised.
   */
  committerDate: string

  /**
   * The date and time when the commit was originally authored.
   *
   * This may differ from `committerDate` if the commit was rebased or amended.
   */
  authorDate?: string
}

/**
 * Represents a single entry in the git log.
 *
 * You can pass extra information in the generic
 * type, and it will be passed back to you in any
 * relevant callback functions.
 */
export type GitLogEntry<T = object> = GitLogEntryBase & T

// ---------------------------------------------------------------------------
// Commit — enriched internal representation (children & isBranchTip added)
// ---------------------------------------------------------------------------

export interface CommitBase {
  /**
   * The unique hash (SHA) identifying the commit.
   */
  hash: string

  /**
   * An array of parent commit hashes (SHA) for this commit.
   * A commit can have multiple parents in the case of merges.
   */
  parents: string[]

  /**
   * An array of child commit hashes (SHA) that
   * reference this commit as a parent.
   *
   * This helps track descendants in the commit graph.
   */
  children: string[]

  /**
   * The name of the branch this commit belongs to.
   */
  branch: string

  /**
   * The commit message describing the changes
   * introduced by this commit.
   */
  message: string

  /**
   * Details of the user who authored
   * the commit.
   */
  author?: CommitAuthor

  /**
   * The date and time when the commit was
   * made by the author, in ISO 8601 format.
   */
  authorDate?: string

  /**
   * The date and time when the commit was
   * committed to the repository, in ISO 8601 format.
   *
   * This may differ from `authorDate` in cases
   * like rebases or amend commits.
   */
  committerDate: string

  /**
   * Indicates whether this commit is the
   * tip (the latest commit) of its branch.
   */
  isBranchTip: boolean
}

/**
 * Represents the author of a Git commit.
 */
export interface CommitAuthor {
  /**
   * The name of the commit author.
   */
  name?: string

  /**
   * The email address of the commit author.
   */
  email?: string
}

/**
 * Represents a commit in the Git history.
 */
export type Commit<T = object> = CommitBase & T

// ---------------------------------------------------------------------------
// Theme types
// ---------------------------------------------------------------------------

export type ThemeMode = 'light' | 'dark'

/**
 * The default theme renders merge nodes
 * with a different theme to make them
 * more distinct from regular commits.
 *
 * The plain theme renders all nodes
 * (except the index pseudo-node) the same.
 */
export type NodeTheme = 'default' | 'plain'

/**
 * Determines how the breakpoints (the styling of
 * the node edges in the graph if a filter is active
 * and is causing breaks in the graph) are rendered.
 */
export type BreakPointTheme = 'slash' | 'dot' | 'ring' | 'zig-zag' | 'line' | 'double-line' | 'arrow'

export type ThemeColours =
  'rainbow-dark' |
  'rainbow-light' |
  'neon-aurora-dark' |
  'neon-aurora-light'

// ---------------------------------------------------------------------------
// Theme functions (returned by the useTheme composable)
// ---------------------------------------------------------------------------

export interface ThemeFunctions {
  /**
   * The current active theme mode.
   */
  theme: ComputedRef<ThemeMode>

  /**
   * An rgb() colour string for
   * text that is being hovered over
   * relative to the currently active theme.
   */
  hoverColour: ComputedRef<string>

  /**
   * An rgb() colour string for
   * text relative to the currently active theme.
   */
  textColour: ComputedRef<string>

  /**
   * An rgb() colour string for the
   * background of a tooltip relative
   * to the currently active theme.
   */
  getTooltipBackground: (commit: Commit) => string

  /**
   * The duration, in milliseconds, of
   * the animation for hover transition effects.
   */
  hoverTransitionDuration: number

  /**
   * Blends an RGB color with a background color to simulate an alpha effect.
   *
   * This function adjusts the given RGB color as if it were semi-transparent
   * over a background, without actually using an alpha channel. The result is
   * a solid RGB color that appears visually similar to an rgba() color with
   * transparency.
   *
   * @param rgb - The colour to shift in `rgb(r, g, b)` format.
   * @param opacity - The simulated alpha channel (range: 0 to 1).
   * @returns The blended color in `rgb(r, g, b)` format.
   */
  shiftAlphaChannel: (rgb: string, opacity: number) => string

  /**
   * Reduces the opacity of the given RGB colour string.
   *
   * @param rgb The colour to reduce the opacity of. Must be in rgb(x, y, z) format.
   * @param opacity The desired opacity value from 0 to 1.
   */
  reduceOpacity: (rgb: string, opacity: number) => string

  /**
   * Gets an rgb() colour string for the given Commit
   * relative to the given colours of the git log.
   *
   * @param commit The commit to get the colour for.
   */
  getCommitColour: (commit: Commit) => string

  /**
   * Gets an rgb() colour string for the given column index
   * relative to the given colours of the git log.
   *
   * @param columnIndex The index to get the colour for.
   */
  getGraphColumnColour: (columnIndex: number) => string

  /**
   * Gets rgb() colour strings for styling a commit node
   * from the given base column colour.
   *
   * @param args Args to get the colour.
   */
  getCommitNodeColours: (args: GetCommitNodeColoursArgs) => CommitNodeColours

  /**
   * Gets an rgb() colour string for the background
   * colour of a graph column when its row is selected.
   *
   * @param columnIndex The index of the column where the node is.
   */
  getGraphColumnSelectedBackgroundColour: (columnIndex: number) => string
}

export interface GetCommitNodeColoursArgs {
  columnColour: string
}

export interface CommitNodeColours {
  backgroundColour: string
  borderColour: string
}

// ---------------------------------------------------------------------------
// Graph orientation
// ---------------------------------------------------------------------------

/**
 * The orientation of the graph.
 *
 * Normal mode draws the graph from left to right,
 * so the checked-out branch is on the left-hand side.
 *
 * Flipped mode inverts the graph in the y-axis so it's
 * drawn from right to left with the checked-out branch
 * on the right-hand side.
 */
export type GraphOrientation = 'normal' | 'flipped'

// ---------------------------------------------------------------------------
// Graph props
// ---------------------------------------------------------------------------

export interface GraphPropsCommon {
  /**
   * The theme to apply to the commit node
   * elements in the graph.
   */
  nodeTheme?: NodeTheme

  /**
   * Determines how the breakpoints (the styling of
   * the node edges in the graph if a filter is active
   * and is causing breaks in the graph) are rendered.
   */
  breakPointTheme?: BreakPointTheme

  /**
   * Enables the graph's horizontal width
   * to be resized.
   *
   * @default false
   */
  enableResize?: boolean

  /**
   * The orientation of the graph.
   *
   * Normal mode draws the graph from left to right,
   * so the checked-out branch is on the left-hand side.
   *
   * Flipped mode inverts the graph in the y-axis so it's
   * drawn from right to left with the checked-out branch
   * on the right-hand side.
   */
  orientation?: GraphOrientation
}

export interface HTMLGridGraphProps<T = unknown> extends GraphPropsCommon {
  /**
   * Whether to show the commit hash
   * to the side of the node in the graph.
   */
  showCommitNodeHashes?: boolean

  /**
   * Whether to show tooltips when hovering
   * over a commit node in the graph.
   */
  showCommitNodeTooltips?: boolean

  /**
   * The height, in pixels, of the background
   * colour of a row that is being previewed
   * or has been selected.
   *
   * You probably only want to set this if
   * you have passed a custom row implementation
   * into the table component that has a different
   * height from the default.
   */
  highlightedBackgroundHeight?: number
}

// ---------------------------------------------------------------------------
// Custom commit node (scoped slot props in Vue)
// ---------------------------------------------------------------------------

export interface CustomCommitNodeProps<T = unknown> {
  /**
   * Details of the commit that this node represents.
   */
  commit: Commit<T>

  /**
   * The colour of the node based on the
   * column that it sits in and any theming.
   */
  colour: string

  /**
   * The zero-based index of the row that
   * this node sits in.
   */
  rowIndex: number

  /**
   * The zero-based index of the column that
   * this node sits in.
   */
  columnIndex: number

  /**
   * The diameter, in pixels, of the
   * commit node. This value is the same
   * as the nodeSize property passed into
   * the graph component.
   */
  nodeSize: number

  /**
   * Denotes whether this node is the
   * "pseudo-node" that is rendered at the
   * top of the graph above the HEAD commit
   * if it is visible based on the current
   * pagination.
   */
  isIndexPseudoNode: boolean
}

// ---------------------------------------------------------------------------
// Custom tooltip (scoped slot props in Vue)
// ---------------------------------------------------------------------------

export interface CustomTooltipProps {
  /**
   * Details of the commit that is
   * being hovered over.
   */
  commit: Commit

  /**
   * The brighter, border colour of the commit based on
   * the current theme that is applied.
   */
  borderColour: string

  /**
   * The darker, background colour of the commit based on
   * the current theme that is applied.
   */
  backgroundColour: string
}

// ---------------------------------------------------------------------------
// GitLog props (main component)
// ---------------------------------------------------------------------------

export interface GitLogCommonProps<T = unknown> {
  /**
   * The git log entries to visualise
   * on the graph.
   */
  entries: GitLogEntry<T>[]

  /**
   * Filters out entries from the log.
   *
   * The log, and any relevant subcomponents,
   * will filter these commits out, so they no
   * longer render, but will change their styling
   * to make it clear that commits are missing.
   */
  filter?: CommitFilter<T>

  /**
   * The variant of the default colour
   * theme to apply to the log.
   */
  theme?: ThemeMode

  /**
   * An array of colours used to colour the
   * log elements such as the graph.
   *
   * One colour will be used for each column
   * in the graph. The number of columns is
   * equal to the maximum number of concurrent
   * active branches in the log.
   *
   * If the number of colours passed is not enough,
   * then the columns will loop back round and start
   * taking from the beginning of the array again.
   */
  colours?: ThemeColours | string[]

  /**
   * Whether to show the names of the elements
   * at the top of the component such as "Graph"
   * or "Commit message" etc.
   */
  showHeaders?: boolean

  /**
   * A function that returns build URI strings
   * to link out to the remote repository on
   * the external Git provider.
   */
  urls?: GitLogUrlBuilder<T>

  /**
   * The height of each commit row, in pixels.
   * Applied across all subcomponents (graph, table, tags).
   *
   * @default DEFAULT_ROW_HEIGHT (40)
   */
  rowHeight?: number

  /**
   * The height of the header row, in pixels.
   * Used when showHeaders is true.
   *
   * @default DEFAULT_HEADER_ROW_HEIGHT (30)
   */
  headerRowHeight?: number

  /**
   * The diameter, in pixels, of the
   * commit node elements rendered on
   * the graph. Also used for the table
   * row background gradient height.
   *
   * @default DEFAULT_NODE_SIZE (20)
   */
  nodeSize?: number

  /**
   * The default width of the graph in pixels.
   *
   * Can be changed dynamically if enableResize is true.
   *
   * @default 300
   */
  defaultGraphWidth?: number

  /**
   * The status of changed files in the
   * Git index.
   */
  indexStatus?: GitLogIndexStatus

  /**
   * Whether to show the Git index
   * "pseudo-commit" entry at the top
   * of the log above the HEAD commit.
   *
   * Represents the local working directory
   * and Git index of the currently checked-out
   * branch. Can optionally show metadata of
   * file statuses via indexStatus.
   */
  showGitIndex?: boolean

  /**
   * A callback function invoked when a commit
   * is selected from the graph or log table.
   *
   * The commit is undefined if it has been
   * unselected.
   *
   * @param commit Details of the selected commit.
   */
  onSelectCommit?: (commit?: Commit<T>) => void

  /**
   * A callback function invoked when a commit
   * is previewed from the graph or log table.
   *
   * The commit is undefined if a commit stops
   * being previewed (e.g. the user has stopped
   * hovering their mouse over a row).
   *
   * @param commit Details of the previewed commit.
   */
  onPreviewCommit?: (commit?: Commit<T>) => void

  /**
   * Enables the row styling across the log
   * elements when a commit is selected.
   *
   * @default true
   */
  enableSelectedCommitStyling?: boolean

  /**
   * Enables the row styling across the log
   * elements when a commit is being previewed.
   *
   * @default true
   */
  enablePreviewedCommitStyling?: boolean

  /**
   * CSS Classes to pass to various underlying
   * elements for custom styling.
   */
  classes?: GitLogStylingProps
}

export interface GitLogProps<T = unknown> extends GitLogCommonProps<T> {
  /**
   * The name of the branch that is
   * currently checked out.
   */
  currentBranch: string

  /**
   * Optional paging information to show
   * a window of the given size from the
   * set of git log entries.
   *
   * This property assumes you are using
   * client-side pagination and that the
   * given entries include the entire Git log
   * history for the repository.
   */
  paging?: GitLogPaging
}

// ---------------------------------------------------------------------------
// Styling
// ---------------------------------------------------------------------------

export interface GitLogStylingProps {
  /**
   * A class name passed to the wrapping
   * container (div) around the log.
   *
   * This includes the branches/tags, the
   * graph and the git log table.
   */
  containerClass?: string

  /**
   * A CSS styling object passed to
   * the wrapping container (div) around
   * the log.
   *
   * This includes the branches/tags, the
   * graph and the git log table.
   */
  containerStyles?: CSSProperties
}

// ---------------------------------------------------------------------------
// Pagination
// ---------------------------------------------------------------------------

export interface GitLogPaging {
  /**
   * The number of rows to show in
   * each page.
   */
  size: number

  /**
   * The page number to show.
   * The first page is page 0.
   */
  page: number
}

/**
 * Internal pagination representation using start/end indices.
 * Converted from the public GitLogPaging (page/size) in the root component.
 */
export interface GraphPaging {
  /**
   * The zero-based index of the row
   * to show from in the log.
   */
  startIndex: number

  /**
   * The zero-based index of the row
   * to show to in the log.
   */
  endIndex: number
}

// ---------------------------------------------------------------------------
// Git index status
// ---------------------------------------------------------------------------

export interface GitLogIndexStatus {
  /**
   * The number of modified files on
   * the checked-out branch according
   * to the Git index.
   */
  modified: number

  /**
   * The number of added files on
   * the checked-out branch according
   * to the Git index.
   */
  added: number

  /**
   * The number of deleted files on
   * the checked-out branch according
   * to the Git index.
   */
  deleted: number
}

// ---------------------------------------------------------------------------
// URL builder
// ---------------------------------------------------------------------------

export interface GitLogUrls {
  /**
   * A resolved URL to a particular commit hash
   * on the external Git provider's remote website.
   */
  commit?: string

  /**
   * A resolved URL to a branch on the external
   * Git provider's remote website.
   */
  branch?: string
}

export interface GitLogUrlBuilderArgs<T = unknown> {
  /**
   * Details of the given commit in context
   * of a URL. E.g. the one you clicked on
   * to link out to the external provider.
   */
  commit: Commit<T>
}

/**
 * A function that builds up URLs to the external
 * Git provider's website to the remote repository
 * of this log.
 *
 * @param args Contextual commit information to help build the URLs.
 */
export type GitLogUrlBuilder<T = unknown> = (args: GitLogUrlBuilderArgs<T>) => GitLogUrls

/**
 * A function that filters the list of commits
 * displayed in the log.
 *
 * @param commits An array of commits currently displayed in the log.
 */
export type CommitFilter<T = unknown> = (commits: Commit<T>[]) => Commit<T>[]

// ---------------------------------------------------------------------------
// Table props
// ---------------------------------------------------------------------------

export interface TableProps {
  /**
   * A timestamp format string passed to DayJS
   * to format the timestamps of the commits
   * in the log table.
   *
   * @default ISO-8601
   */
  timestampFormat?: string

  /**
   * A class name passed to the table's
   * wrapping container element.
   */
  className?: string

  /**
   * A CSS styling object passed to
   * the various elements of the table.
   */
  styles?: GitLogTableStylingProps
}

/**
 * Props passed to the custom table row scoped slot.
 */
export interface CustomTableRowProps {
  /**
   * Details of the commit belonging
   * to this row.
   */
  commit: Commit

  /**
   * Whether the commit in this row
   * has been selected in the log.
   *
   * i.e. Has been clicked on.
   */
  selected: boolean

  /**
   * Whether the commit in this row
   * has been previewed in the log.
   *
   * i.e. Is being hovered over.
   */
  previewed: boolean

  /**
   * The colour of the background as is
   * normally applied to the table row.
   *
   * This colour changes based on whether
   * the row is previewed or selected
   * and on the chosen theme or array of
   * graph colours.
   */
  backgroundColour: string
}

export interface GitLogTableStylingProps {
  /**
   * A CSS styling object passed to
   * the container element of the table.
   */
  table?: CSSProperties

  /**
   * A CSS styling object passed to
   * the wrapping element around the table headers.
   */
  thead?: CSSProperties

  /**
   * A CSS styling object passed to
   * each table row element.
   */
  tr?: CSSProperties

  /**
   * A CSS styling object passed to
   * each table data element.
   */
  td?: CSSProperties
}
