/**
 * All layout constants are expressed as pixel values calibrated for
 * a root font-size of 16px (1rem = 16px). They are converted to rem
 * in stylesheets and inline styles so that the entire component scales
 * proportionally when the root font-size changes.
 */


/**
 * The default height of each row across
 * all subcomponents, in pixels.
 */
export const DEFAULT_HEADER_ROW_HEIGHT = 30

/**
 * The height of each row across
 * all subcomponents, in pixels.
 */
export const DEFAULT_ROW_HEIGHT = 40

/**
 * The default width and height of
 * a commit node on the graph.
 */
export const DEFAULT_NODE_SIZE = 20

/**
 * The default width of each column in the graph,
 * in pixels. Each column corresponds to a concurrent
 * branch in the graph.
 */
export const DEFAULT_GRAPH_COLUMN_WIDTH = 24

/**
 * The width of the borders on a commit node.
 */
export const NODE_BORDER_WIDTH = 2
// TODO: extract a LINE_BORDER_WIDTH constant for graph edges
// (vertical lines, curves, etc.) — currently hardcoded as 2px
// in multiple components and style blocks.

/**
 * The number of pixels to offset the background
 * element/colour by so that it has padding around
 * the commit node on the graph.
 */
export const BACKGROUND_HEIGHT_OFFSET = 16

/**
 * The default number of extra rows rendered
 * above and below the visible scroll window.
 */
export const DEFAULT_SCROLL_BUFFER = 20

/**
 * The size of the SVG curved line and
 * the lengths of the accompanying vertical
 * lines.
 */
export const CURVE_SIZE = 24
