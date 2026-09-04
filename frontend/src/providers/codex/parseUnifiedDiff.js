/**
 * Tiny parser for unified-diff bodies (``@@ -a,b +c,d @@`` style)
 * into the same hunk shape used by Claude Code's structured patches::
 *
 *     [{ oldStart, oldLines, newStart, newLines, lines: ['+...', '-...', ' ...'] }]
 *
 * That shape is what ``utils/patchUtils.js → reconstructFromHunks``
 * consumes to produce the per-side line maps for ``DiffEditor``.
 *
 * Codex's ``FileChange`` event ships exactly this format inside
 * ``changes[path].unified_diff`` (no leading ``--- a/foo`` /
 * ``+++ b/foo`` file headers — just the ``@@`` hunks and their bodies).
 * We parse only what we receive; if either of the line-range counts is
 * omitted (the standard ``@@ -1 +1 @@`` shorthand for a single line),
 * we default it to ``1``.
 */

const HUNK_HEADER_RE = /^@@\s*-(\d+)(?:,(\d+))?\s+\+(\d+)(?:,(\d+))?\s*@@/

/**
 * @param {string} unifiedDiff
 * @returns {Array<{
 *     oldStart: number, oldLines: number,
 *     newStart: number, newLines: number,
 *     lines: string[],
 * }>} Empty array when the input doesn't contain a single recognisable
 *     hunk — callers treat this as "no diff to display".
 */
export function parseUnifiedDiff(unifiedDiff) {
    if (typeof unifiedDiff !== 'string' || !unifiedDiff) return []
    const lines = unifiedDiff.split('\n')
    // Drop the trailing empty string produced by a final '\n' in the input
    // (the standard shape Codex emits). Without this, the empty string lands
    // in ``current.lines`` and ``jsdiff.applyPatch`` rejects the hunk as
    // corrupted — silently breaking the full-file diff path in
    // ApplyPatchContent. A truly empty context line is encoded as ' '
    // (space + nothing) in unified diffs, never as a bare ''.
    if (lines.length && lines[lines.length - 1] === '') lines.pop()
    const hunks = []
    let current = null

    for (const line of lines) {
        const header = HUNK_HEADER_RE.exec(line)
        if (header) {
            current = {
                oldStart: parseInt(header[1], 10),
                oldLines: header[2] != null ? parseInt(header[2], 10) : 1,
                newStart: parseInt(header[3], 10),
                newLines: header[4] != null ? parseInt(header[4], 10) : 1,
                lines: [],
            }
            hunks.push(current)
            continue
        }
        if (!current) continue
        if (line.startsWith('---') || line.startsWith('+++')) continue
        // ``\ No newline at end of file`` markers etc. — pass them
        // through untouched; ``reconstructFromHunks`` skips them.
        current.lines.push(line)
    }

    return hunks
}
