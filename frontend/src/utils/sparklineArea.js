// Area under a stats curve (visual refresh step 7a,
// docs/plans/2026-09-30-stats-motion-design.md §6.2).

/**
 * Close a polyline points string ("x,y x,y ...") into a polygon along the `baseY` line:
 * appends "<last x>,<baseY> <first x>,<baseY>". Empty points give an empty string.
 */
export function areaPoints(points, baseY) {
    const list = points.trim().split(/\s+/).filter(Boolean)
    if (!list.length) return ''
    const firstX = list[0].split(',')[0]
    const lastX = list[list.length - 1].split(',')[0]
    return `${list.join(' ')} ${lastX},${baseY} ${firstX},${baseY}`
}
