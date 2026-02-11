import type { CommitNodeLocation } from '../../data/types'
import type { Commit } from '../../types'
import type { GraphMatrix } from './GraphMatrix'

export interface GraphMatrixBuilderProps {
  graphColumns: number
  commits: Commit[]
  positions: Map<string, CommitNodeLocation>
  visibleCommits: number
  headCommit?: Commit
  headCommitHash?: string
  isIndexVisible: boolean
}

export interface GraphBreakPointCheck {
  location: CommitNodeLocation
  check: (matrix: GraphMatrix) => boolean
  position: GraphBreakPointPosition
}

export type GraphBreakPointPosition = 'top' | 'bottom'

/**
 * Represents the state of a single column cell in the graph matrix.
 *
 * Each cell can contain graphical elements such as nodes, lines, or curves
 * that together form the visual representation of the git graph.
 */
export interface GraphColumnState {
  /**
   * Indicates that this column contains
   * a commit node.
   */
  isNode?: boolean

  /**
   * Indicates that this column contains
   * a horizontal line that spans the full
   * width of the column to connect two
   * nodes via branching or merging.
   */
  isHorizontalLine?: boolean

  /**
   * Indicates that this column contains
   * a vertical line that spans the full
   * height of the column to connect two
   * nodes via branching or merging.
   */
  isVerticalLine?: boolean

  /**
   * Indicates that this column contains
   * a vertical dotted line that spans the full
   * height of the column to connect the HEAD
   * node of the current branch to the
   * pseudo index commit node of the git index.
   */
  isVerticalIndexLine?: boolean

  /**
   * Indicates that this column is included in
   * the last row of all visible commits that
   * are currently being rendered.
   *
   * This should be false if the last row is
   * actually the first commit in the git log entries.
   */
  isLastRow?: boolean

  /**
   * Indicates that this column is included in
   * the first row of all visible commits that
   * are currently being rendered.
   *
   * This should be false if the first row is
   * actually the last commit in the git log entries.
   */
  isFirstRow?: boolean

  /**
   * The graph row column indices of the commit
   * nodes that a merge came from.
   *
   * This will only be populated for columns that
   * have commit nodes in. Multiple numbers indicate
   * that multiple other nodes merge into this one.
   */
  mergeSourceColumns?: number[]

  /**
   * Indicates that the column in the row above
   * the one that this column resides in is empty.
   * Which is to say, it contains no graphical elements
   * such as nodes or lines.
   *
   * This indicates that the commit node in this column
   * has no parent commit in the row above (visually).
   */
  isColumnAboveEmpty?: boolean

  /**
   * Indicates that the column in the row below
   * the one that this column resides in is empty.
   * Which is to say, it contains no graphical elements
   * such as nodes or lines.
   *
   * This indicates that the commit node in this column
   * has no child commit in the row below (visually).
   */
  isColumnBelowEmpty?: boolean

  /**
   * Indicates that this column contains
   * a curved line that starts at the
   * middle of the left edge of the column
   * and stops at the middle of the bottom
   * edge of the column.
   */
  isLeftDownCurve?: boolean

  /**
   * Indicates that this column contains
   * a curved line that starts at the
   * middle of the left edge of the column
   * and stops at the middle of the top
   * edge of the column.
   */
  isLeftUpCurve?: boolean

  /**
   * Indicates that the elements in this
   * column (whether they be lines or a node)
   * are part of a skeleton placeholder to
   * indicate that there is currently no data
   * to show on the graph based on current
   * filtering.
   */
  isPlaceholderSkeleton?: boolean

  isTopBreakPoint?: boolean

  isBottomBreakPoint?: boolean
}
